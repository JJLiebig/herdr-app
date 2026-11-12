//! Back and forward through the panes this screen showed, as in a browser:
//! `nav.back` / `nav.forward`, and the mouse's back and forward buttons.

/// Visited panes, oldest first; `at` is the one shown.
#[derive(Default)]
pub(super) struct NavHistory {
    panes: Vec<String>,
    at: usize,
}

const LIMIT: usize = 100;

impl NavHistory {
    /// The shown pane changed. Going somewhere new drops the forward entries;
    /// arriving where back/forward pointed changes nothing.
    pub(super) fn visit(&mut self, pane: &str) {
        if self.panes.get(self.at).is_some_and(|p| p == pane) {
            return;
        }
        self.panes.truncate(self.at + 1);
        self.panes.push(pane.to_owned());
        if self.panes.len() > LIMIT {
            self.panes.remove(0);
        }
        self.at = self.panes.len() - 1;
    }

    /// The pane to show for one step back (or forward), skipping panes that are gone.
    pub(super) fn step(&mut self, back: bool, exists: impl Fn(&str) -> bool) -> Option<String> {
        self.prune(exists);
        let target = if back { self.at.checked_sub(1)? } else { self.at + 1 };
        let pane = self.panes.get(target)?.clone();
        self.at = target;
        Some(pane)
    }

    /// Drops closed panes and the repeats their removal leaves side by side.
    fn prune(&mut self, exists: impl Fn(&str) -> bool) {
        let mut kept: Vec<String> = Vec::with_capacity(self.panes.len());
        let mut at = 0;
        for (i, pane) in self.panes.drain(..).enumerate() {
            if exists(&pane) && kept.last() != Some(&pane) {
                kept.push(pane);
            }
            if i <= self.at {
                at = kept.len().saturating_sub(1);
            }
        }
        self.panes = kept;
        self.at = at;
    }
}

#[cfg(test)]
mod tests {
    use super::NavHistory;

    fn visited(panes: &[&str]) -> NavHistory {
        let mut h = NavHistory::default();
        for p in panes {
            h.visit(p);
        }
        h
    }

    #[test]
    fn back_and_forward_walk_the_visits_and_a_new_visit_drops_forward() {
        let mut h = visited(&["a", "b", "c"]);
        assert_eq!(h.step(true, |_| true).as_deref(), Some("b"));
        h.visit("b");
        assert_eq!(h.step(true, |_| true).as_deref(), Some("a"));
        assert_eq!(h.step(true, |_| true), None);
        assert_eq!(h.step(false, |_| true).as_deref(), Some("b"));
        h.visit("b");
        h.visit("d");
        assert_eq!(h.step(false, |_| true), None);
        assert_eq!(h.step(true, |_| true).as_deref(), Some("b"));
    }

    #[test]
    fn closed_panes_are_skipped() {
        let mut h = visited(&["a", "b", "a", "c"]);
        assert_eq!(h.step(true, |p| p != "a").as_deref(), Some("b"));
        assert_eq!(h.step(true, |p| p != "a"), None);
        assert_eq!(h.step(false, |p| p != "a").as_deref(), Some("c"));
    }
}