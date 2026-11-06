//! User-installed links are explicit asset locations, confined by AppAssets
//! after following the link. Ordinary shell applications keep their own policy.
use gpui::{AssetSource, SharedString};
use gpui_shell::AppAssets;
use std::{
    borrow::Cow,
    path::{Component, Path, PathBuf},
};

pub struct Assets(pub PathBuf);

impl Assets {
    fn source(&self, path: &str) -> (AppAssets, String) {
        let normalized = AppAssets::cache_key(path);
        let parts: Vec<_> = Path::new(normalized.as_ref()).components().collect();
        // Traversal cannot select a grant; use the existing root-relative spelling.
        if !parts
            .iter()
            .all(|p| matches!(p, Component::Normal(_) | Component::CurDir))
            || parts.first().is_none_or(|p| p.as_os_str() != "mods")
        {
            return (AppAssets::new(self.0.clone()), path.to_owned());
        }
        let mut prefix = self.0.clone();
        let mut selected = None;
        for (i, part) in parts.iter().enumerate() {
            prefix.push(part);
            if std::fs::symlink_metadata(&prefix).is_ok_and(|m| m.is_symlink()) {
                // The installed link, not a mod-supplied path string, grants
                // this location. AppAssets still bounds all subsequent reads.
                if let Ok(target) = prefix.canonicalize() {
                    let suffix: PathBuf = parts[i + 1..].iter().collect();
                    selected = if target.is_dir() && !suffix.as_os_str().is_empty() {
                        Some((target, suffix))
                    } else {
                        target.parent().map(|parent| {
                            (
                                parent.to_path_buf(),
                                if suffix.as_os_str().is_empty() {
                                    PathBuf::from(target.file_name().unwrap())
                                } else {
                                    PathBuf::from(target.file_name().unwrap()).join(suffix)
                                },
                            )
                        })
                    };
                }
            }
        }
        selected
            .map(|(root, suffix)| (AppAssets::new(root), suffix.to_string_lossy().into_owned()))
            .unwrap_or_else(|| (AppAssets::new(self.0.clone()), path.to_owned()))
    }
}

impl AssetSource for Assets {
    fn load(&self, path: &str) -> anyhow::Result<Option<Cow<'static, [u8]>>> {
        let (source, path) = self.source(path);
        source.load(&path)
    }
    fn list(&self, path: &str) -> anyhow::Result<Vec<SharedString>> {
        let (source, path) = self.source(path);
        source.list(&path)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

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
    fn linked_assets_load_and_list_without_granting_unlinked_files() {
        let root = sandbox("assets");
        let home = root.join("home");
        let external = root.join("external");
        std::fs::write(external.join("mod/icon.svg"), b"<svg/>").unwrap();
        std::fs::write(external.join("mod/picture.png"), b"png bytes").unwrap();
        std::fs::write(external.join("chosen.svg"), b"chosen").unwrap();
        std::fs::write(external.join("secret.svg"), b"secret").unwrap();
        link(&external.join("mod"), &home.join("mods/probe"));
        link(&external.join("chosen.svg"), &external.join("mod/file.svg"));
        let assets = Assets(home.clone());
        for (path, bytes) in [
            ("mods/probe/icon.svg", b"<svg/>".as_slice()),
            ("mods/probe/picture.png", b"png bytes"),
            ("mods/probe/file.svg", b"chosen"),
        ] {
            assert_eq!(assets.load(path).unwrap().unwrap().as_ref(), bytes);
        }
        assert!(
            assets
                .list("mods/probe")
                .unwrap()
                .iter()
                .any(|name| name.as_ref() == "icon.svg")
        );
        assert!(assets.load("mods/probe/../secret.svg").is_err());
        assert!(assets.load("../external/secret.svg").is_err());
        let absolute = assets.load(external.join("secret.svg").to_str().unwrap());
        assert!(!matches!(absolute, Ok(Some(bytes)) if bytes.as_ref() == b"secret"));
        link(&external, &home.join("ungranted"));
        assert!(assets.load("ungranted/secret.svg").is_err());
        assert!(assets.load("mods/probe/missing.svg").unwrap().is_none());
        // Retargeting changes the bytes at the same logical/cache path.
        std::fs::remove_file(external.join("mod/file.svg")).unwrap();
        link(
            &external.join("mod/icon.svg"),
            &external.join("mod/file.svg"),
        );
        assert_eq!(
            assets
                .load("mods/probe/file.svg")
                .unwrap()
                .unwrap()
                .as_ref(),
            b"<svg/>"
        );
        std::fs::remove_dir_all(root).unwrap();
    }
}
