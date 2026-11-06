//! Watches linked configuration targets and translates events back to home paths.
use notify::{RecursiveMode, Watcher};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

type Links = HashSet<(PathBuf, PathBuf)>;

// Include every link in a chain: watching only canonical targets misses an
// intermediate "current" link being switched to a different release.
fn link_targets(path: &Path, alias: &Path, out: &mut Links, depth: usize) {
    if depth == 40 {
        return;
    } // Match persist's symlink-cycle bound.
    for ancestor in path.ancestors() {
        if ancestor.symlink_metadata().is_ok_and(|m| m.is_symlink()) {
            out.insert((alias.to_path_buf(), ancestor.to_path_buf()));
            if let Ok(target) = std::fs::read_link(ancestor) {
                let target = ancestor
                    .parent()
                    .unwrap_or(Path::new(""))
                    .join(target)
                    .join(path.strip_prefix(ancestor).unwrap());
                link_targets(&target, alias, out, depth + 1);
            }
            return;
        }
    }
    out.insert((alias.to_path_buf(), path.to_path_buf()));
}

fn links(home: &Path) -> Links {
    fn visit(home: &Path, rel: PathBuf, ancestors: &mut HashSet<PathBuf>, out: &mut Links) {
        if !crate::files::watched(&rel) {
            return;
        }
        let path = home.join(&rel);
        if path.symlink_metadata().is_ok_and(|m| m.is_symlink()) {
            link_targets(&path, &rel, out, 0);
        }
        let Ok(real) = path.canonicalize() else {
            return;
        };
        if !real.is_dir() || !ancestors.insert(real.clone()) {
            return;
        }
        if let Ok(entries) = std::fs::read_dir(path) {
            for entry in entries.flatten() {
                visit(home, rel.join(entry.file_name()), ancestors, out);
            }
        }
        ancestors.remove(&real);
    }
    let mut out = Links::new();
    for name in [
        "mods",
        "layouts",
        "themes",
        "keys.json",
        "settings.json",
        "programs.json",
    ] {
        visit(home, name.into(), &mut HashSet::new(), &mut out);
    }
    out
}

fn paths(home: &Path, links: &Links, event: &notify::Event) -> Vec<PathBuf> {
    let mut changed = HashSet::new();
    for path in &event.paths {
        if let Ok(rel) = path.strip_prefix(home) {
            if crate::files::watched(rel) {
                changed.insert(rel.to_path_buf());
            }
        }
        for (alias, target) in links {
            if target.starts_with(path) {
                changed.insert(alias.clone());
            }
            if let Ok(suffix) = path.strip_prefix(target) {
                let rel = alias.join(suffix);
                if crate::files::watched(&rel) {
                    changed.insert(rel);
                }
            }
        }
    }
    changed.into_iter().collect()
}

/// Owns the OS watcher on a worker thread, so link changes can rebuild watches
/// without locking inside notify's callback. The receiver owns its lifetime.
pub fn watch(home: &Path) -> Option<async_channel::Receiver<crate::files::Changed>> {
    let home = home.canonicalize().ok()?;
    let (events_tx, events_rx) = std::sync::mpsc::channel();
    let mut watcher = notify::recommended_watcher(move |event| {
        let _ = events_tx.send(event);
    })
    .ok()?;
    watcher.watch(&home, RecursiveMode::Recursive).ok()?;
    let (tx, rx) = async_channel::unbounded();
    let initial = links(&home);
    let initial_watches = refresh(&mut watcher, &home, &initial, HashSet::new());
    std::thread::spawn(move || {
        let mut linked = initial;
        let mut watching = initial_watches;
        while !tx.is_closed() {
            let event = match events_rx.recv_timeout(std::time::Duration::from_millis(250)) {
                Ok(Ok(event)) if !matches!(event.kind, notify::EventKind::Access(_)) => event,
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                _ => continue,
            };
            let mut changed = paths(&home, &linked, &event);
            // Parent watches catch atomic saves and link retargeting. Only
            // rediscover on structural events; ordinary edits need no walk.
            if !changed.is_empty()
                && !matches!(
                    event.kind,
                    notify::EventKind::Modify(notify::event::ModifyKind::Data(_))
                )
            {
                let next = links(&home);
                for (alias, target) in &next {
                    if !linked.contains(&(alias.clone(), target.clone())) {
                        changed.push(alias.clone());
                    }
                }
                // A target directory can be replaced at the same spelling.
                // Its old OS watch follows the old inode/handle, not the name.
                watching.retain(|(path, _)| {
                    let replaced = event.paths.iter().any(|p| path.starts_with(p));
                    if replaced {
                        let _ = watcher.unwatch(path);
                    }
                    !replaced
                });
                watching = refresh(&mut watcher, &home, &next, watching);
                linked = next;
            }
            if !changed.is_empty() {
                let _ = tx.send_blocking(changed);
            }
        }
    });
    Some(rx)
}

fn refresh(
    watcher: &mut notify::RecommendedWatcher,
    home: &Path,
    links: &Links,
    old: HashSet<(PathBuf, bool)>,
) -> HashSet<(PathBuf, bool)> {
    let mut desired = HashSet::new();
    for (_, target) in links {
        if target.is_dir() && target != home {
            desired.insert((target.clone(), true));
        }
        if let Some(parent) = target.ancestors().skip(1).find(|p| p.is_dir()) {
            if parent != home {
                desired.insert((parent.to_path_buf(), false));
            }
        }
    }
    // A recursive target also serves as the parent watch of a linked file.
    let recursive: HashSet<_> = desired
        .iter()
        .filter(|(_, recursive)| *recursive)
        .map(|(p, _)| p.clone())
        .collect();
    desired.retain(|(p, r)| *r || !recursive.contains(p));
    for (path, _) in old.difference(&desired) {
        let _ = watcher.unwatch(path);
    }
    let mut active = HashSet::new();
    for (path, recursive) in desired {
        if old.contains(&(path.clone(), recursive))
            || watcher
                .watch(
                    &path,
                    if recursive {
                        RecursiveMode::Recursive
                    } else {
                        RecursiveMode::NonRecursive
                    },
                )
                .is_ok()
        {
            active.insert((path, recursive));
        } else {
            tracing::warn!("cannot watch linked configuration {}", path.display());
        }
    }
    active
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linked_mods::tests::{link, sandbox};

    fn wait(rx: &async_channel::Receiver<crate::files::Changed>, expected: &[&str]) {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        let mut remaining: HashSet<PathBuf> = expected.iter().map(PathBuf::from).collect();
        while !remaining.is_empty() {
            while let Ok(paths) = rx.try_recv() {
                remaining.retain(|wanted| !paths.iter().any(|p| wanted.starts_with(p)));
            }
            assert!(
                std::time::Instant::now() < deadline,
                "missing changes: {remaining:?}"
            );
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        while rx.try_recv().is_ok() {}
    }

    #[test]
    fn linked_targets_reload_on_edits_atomic_saves_new_links_and_retargeting() {
        let root = sandbox("watch");
        let home = root.join("home");
        let external = root.join("external");
        let module = external.join("mod");
        std::fs::write(module.join("main.js"), "one").unwrap();
        std::fs::write(external.join("keys.json"), "{}").unwrap();
        link(&module, &home.join("mods/one"));
        link(&module, &home.join("mods/two"));
        link(&external.join("keys.json"), &home.join("keys.json"));
        let rx = watch(&home).unwrap();
        std::fs::write(module.join("main.js"), "two").unwrap();
        wait(&rx, &["mods/one/main.js", "mods/two/main.js"]);
        // Editors replace a target file without touching its installed link.
        crate::persist::write_atomic(&home.join("keys.json"), "{\"new\":true}").unwrap();
        wait(&rx, &["keys.json"]);
        std::fs::create_dir_all(module.join("nested")).unwrap();
        wait(&rx, &["mods/one/nested", "mods/two/nested"]);
        std::fs::write(module.join("nested/ui.js"), "nested").unwrap();
        wait(&rx, &["mods/one/nested/ui.js", "mods/two/nested/ui.js"]);
        // A file link inside a plain mod gets the same translation.
        std::fs::create_dir_all(home.join("mods/plain")).unwrap();
        link(&module.join("main.js"), &home.join("mods/plain/main.js"));
        wait(&rx, &["mods/plain/main.js"]);
        std::fs::write(module.join("main.js"), "three").unwrap();
        wait(
            &rx,
            &["mods/plain/main.js", "mods/one/main.js", "mods/two/main.js"],
        );
        let other = external.join("other");
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(other.join("main.js"), "other").unwrap();
        #[cfg(windows)]
        std::fs::remove_dir(home.join("mods/one")).unwrap();
        #[cfg(unix)]
        std::fs::remove_file(home.join("mods/one")).unwrap();
        link(&other, &home.join("mods/one"));
        wait(&rx, &["mods/one"]);
        std::fs::write(other.join("main.js"), "updated").unwrap();
        wait(&rx, &["mods/one/main.js"]);
        std::fs::remove_dir_all(&other).unwrap();
        wait(&rx, &["mods/one"]);
        std::fs::create_dir_all(&other).unwrap();
        wait(&rx, &["mods/one"]);
        std::fs::write(other.join("main.js"), "recreated").unwrap();
        wait(&rx, &["mods/one/main.js"]);
        // An intermediate release link can move without touching home.
        link(&module, &external.join("current"));
        link(&external.join("current"), &home.join("mods/chain"));
        wait(&rx, &["mods/chain"]);
        #[cfg(windows)]
        std::fs::remove_dir(external.join("current")).unwrap();
        #[cfg(unix)]
        std::fs::remove_file(external.join("current")).unwrap();
        link(&other, &external.join("current"));
        wait(&rx, &["mods/chain"]);
        std::fs::write(other.join("main.js"), "chain retargeted").unwrap();
        wait(&rx, &["mods/chain/main.js"]);
        // Install a dangling file link before even its parent exists.
        let future = external.join("future/deep/main.js");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&future, home.join("mods/plain/future.js")).unwrap();
        #[cfg(windows)]
        std::os::windows::fs::symlink_file(&future, home.join("mods/plain/future.js")).unwrap();
        wait(&rx, &["mods/plain/future.js"]);
        std::fs::create_dir_all(future.parent().unwrap()).unwrap();
        std::fs::write(&future, "created later").unwrap();
        wait(&rx, &["mods/plain/future.js"]);
        std::fs::write(&future, "edited later").unwrap();
        wait(&rx, &["mods/plain/future.js"]);
        drop(rx);
        // Wait for worker disposal before removing watched directories on Windows.
        std::thread::sleep(std::time::Duration::from_millis(300));
        std::fs::remove_dir_all(root).unwrap();
    }
}
