//! Track requested assets for linked-directory invalidation; upstream owns reads.
use gpui::{AssetSource, SharedString};
use gpui_shell::AppAssets;
use std::{
    borrow::Cow,
    path::PathBuf,
};

// Remember requested assets until invalidation, including files subsequently
// absent from a retargeted directory. Disk enumeration cannot find those keys.
static LOADED: std::sync::OnceLock<std::sync::Mutex<std::collections::HashSet<PathBuf>>> =
    std::sync::OnceLock::new();
fn loaded() -> std::sync::MutexGuard<'static, std::collections::HashSet<PathBuf>> {
    LOADED.get_or_init(Default::default).lock().unwrap()
}
pub fn changed_assets(changed: &[PathBuf]) -> Vec<PathBuf> {
    let mut assets = Vec::new();
    loaded().retain(|path| {
        if changed.iter().any(|prefix| path.starts_with(prefix)) {
            assets.push(path.clone());
            false
        } else {
            true
        }
    });
    assets
}

pub struct Assets(pub PathBuf);

impl AssetSource for Assets {
    fn image_resource(&self, resource: &gpui::Resource) -> gpui::Resource {
        AppAssets::new(self.0.clone()).image_resource(resource)
    }

    fn load(&self, path: &str) -> anyhow::Result<Option<Cow<'static, [u8]>>> {
        let key = AppAssets::cache_key(path);
        let result = AppAssets::new(self.0.clone()).following_links().load(path);
        if matches!(&result, Ok(Some(_))) {
            loaded().insert(PathBuf::from(key.as_ref()));
        }
        result
    }
    fn list(&self, path: &str) -> anyhow::Result<Vec<SharedString>> {
        AppAssets::new(self.0.clone()).following_links().list(path)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::path::Path;

    pub fn sandbox(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("herdr-linked-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(path.join("home/mods")).unwrap();
        std::fs::create_dir_all(path.join("external/mod")).unwrap();
        path
    }

    pub fn link(target: &Path, alias: &Path) {
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, alias).unwrap();
        #[cfg(windows)]
        if target.is_dir() {
            std::os::windows::fs::symlink_dir(target, alias).unwrap();
        } else {
            std::os::windows::fs::symlink_file(target, alias).unwrap();
        }
    }

    #[test]
    fn linked_assets_load_list_and_track_removed_files() {
        let root = sandbox("assets");
        let home = root.join("home");
        let external = root.join("external");
        std::fs::write(external.join("mod/icon.svg"), b"<svg/>").unwrap();
        std::fs::write(external.join("mod/picture.png"), b"png bytes").unwrap();
        std::fs::write(external.join("chosen.svg"), b"chosen").unwrap();
        std::fs::write(external.join("secret.svg"), b"secret").unwrap();
        link(&external.join("mod"), &home.join("mods/linked-assets-probe"));
        link(&external.join("chosen.svg"), &external.join("mod/file.svg"));
        let assets = Assets(home.clone());
        for (path, bytes) in [
            ("mods/linked-assets-probe/icon.svg", b"<svg/>".as_slice()),
            ("mods/linked-assets-probe/picture.png", b"png bytes"),
            ("mods/linked-assets-probe/file.svg", b"chosen"),
        ] {
            assert_eq!(assets.load(path).unwrap().unwrap().as_ref(), bytes);
        }
        assert!(
            assets
                .list("mods/linked-assets-probe")
                .unwrap()
                .iter()
                .any(|name| name.as_ref() == "icon.svg")
        );
        assert!(assets.load("mods/linked-assets-probe/../secret.svg").is_err());
        assert!(assets.load("../external/secret.svg").is_err());
        let absolute = assets.load(external.join("secret.svg").to_str().unwrap());
        assert!(!matches!(absolute, Ok(Some(bytes)) if bytes.as_ref() == b"secret"));
        // The app adopts upstream's deliberate home-link policy, including
        // links outside mods; traversal strings above still cannot escape.
        link(&external, &home.join("linked-home-assets"));
        assert_eq!(assets.load("linked-home-assets/chosen.svg").unwrap().unwrap().as_ref(), b"chosen");
        assert!(assets.list("linked-home-assets").unwrap().iter().any(|name| name.as_ref() == "chosen.svg"));
        assert!(assets.load("mods/linked-assets-probe/missing.svg").unwrap().is_none());
        // Retargeting changes the bytes at the same logical/cache path.
        std::fs::remove_file(external.join("mod/file.svg")).unwrap();
        link(
            &external.join("mod/icon.svg"),
            &external.join("mod/file.svg"),
        );
        assert_eq!(
            assets
                .load("mods/linked-assets-probe/file.svg")
                .unwrap()
                .unwrap()
                .as_ref(),
            b"<svg/>"
        );
        std::fs::remove_file(external.join("mod/picture.png")).unwrap();
        assert!(changed_assets(&["mods/linked-assets-probe".into()]).contains(&"mods/linked-assets-probe/picture.png".into()));
        assert!(assets.load("mods/linked-assets-probe/picture.png").unwrap().is_none());
        std::fs::remove_dir_all(root).unwrap();
    }
}
