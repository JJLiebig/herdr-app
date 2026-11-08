//! Ephemeral completion alerts for a top-level agent and its live descendants.
use crate::model::{Alert, Model};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

/// A held completion waits for workers, then for a quiet team interval.
enum Phase {
    WorkingDescendants,
    QuietSince(Instant),
}

struct Held {
    completion_turn: u64,
    phase: Phase,
}

#[derive(Default)]
pub(super) struct TeamAlerts {
    held: HashMap<String, Held>,
}

pub(super) fn root_agent(live: &HashMap<String, Value>, addr: &str) -> String {
    let mut cur = addr.to_owned();
    for _ in 0..32 {
        match live.get(&cur).and_then(|r| r["parent"].as_str()) {
            Some(p) if live.contains_key(p) => cur = p.to_owned(),
            _ => break,
        }
    }
    cur
}

impl TeamAlerts {
    pub(super) fn poll(&mut self, model: &Model, live: &HashMap<String, Value>, visible: &HashSet<String>, now: Instant) -> Vec<(String, Alert, bool)> {
        self.held.retain(|addr, _| live.contains_key(addr) && model.pane_of(addr).is_some());
        let working_roots: HashSet<String> = live.iter()
            .filter(|(_, rec)| rec["status"].as_str() == Some("working"))
            .map(|(addr, _)| root_agent(live, addr)).collect();
        // Turn counters retain short working bursts coalesced between ticks.
        let active_roots: HashSet<String> = live.iter()
            .filter(|(addr, rec)| rec["parent"].as_str().is_some_and(|p| !p.is_empty())
                && rec["turn"].as_u64().unwrap_or(0) > model.agents.get(*addr).map_or(0, |a| a.turn))
            .map(|(addr, _)| root_agent(live, addr)).collect();
        let mut alerts = Vec::new();
        for (addr, rec) in live {
            let word = rec["status"].as_str();
            let child = rec["parent"].as_str().is_some_and(|p| !p.is_empty());
            if child || model.pane_of(addr).is_none() {
                self.held.remove(addr);
                continue;
            }
            let looking = visible.contains(addr);
            let prev = model.agents.get(addr).and_then(|a| a.word.as_deref());
            // A held finish uses attention at delivery; ordinary finishes keep Herdr's rule.
            let turn = rec["turn"].as_u64().unwrap_or(0);
            let resumed = self.held.get(addr).is_some_and(|h| turn > h.completion_turn);
            if resumed { self.held.remove(addr); }
            let transition = model.alert_for(addr, false, if resumed { Some("working") } else { prev }, word, false);
            if matches!(word, None | Some("working" | "blocked" | "error")) {
                self.held.remove(addr);
                if transition == Some(Alert::Blocked) {
                    alerts.push((addr.clone(), Alert::Blocked, looking));
                }
                continue;
            }
            if let Some(held) = self.held.get_mut(addr) {
                if working_roots.contains(addr) {
                    held.phase = Phase::WorkingDescendants;
                } else if active_roots.contains(addr) {
                    held.phase = Phase::QuietSince(now);
                } else {
                    match &held.phase {
                        Phase::WorkingDescendants => held.phase = Phase::QuietSince(now),
                        Phase::QuietSince(since) if now.duration_since(*since) >= Duration::from_secs(3) => {
                            self.held.remove(addr);
                            alerts.push((addr.clone(), Alert::Done, looking));
                        }
                        _ => {}
                    }
                }
            } else if transition == Some(Alert::Done) {
                if working_roots.contains(addr) {
                    // Core emits done and its turn increment separately. Account
                    // for that original increment before detecting resumed work.
                    let completion_turn = if !resumed && prev == Some("working") {
                        turn.max(model.agents.get(addr).map_or(0, |a| a.turn).saturating_add(1))
                    } else { turn };
                    self.held.insert(addr.clone(), Held { completion_turn, phase: Phase::WorkingDescendants });
                } else if !looking {
                    alerts.push((addr.clone(), Alert::Done, false));
                }
            }
        }
        alerts
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Tab;
    use serde_json::json;

    struct Team {
        model: Model,
        live: HashMap<String, Value>,
        alerts: TeamAlerts,
        now: Instant,
    }
    impl Team {
        fn new() -> Self {
            let mut model = Model::default();
            model.add_space("s", "team", "/tmp", Tab::new("tab", "team", "pane", "remote/t1"));
            let mut t = Self { model, live: HashMap::new(), alerts: TeamAlerts::default(), now: Instant::now() };
            t.set("remote/t1", "working", None);
            t.tick(0, false);
            t
        }
        fn set(&mut self, addr: &str, word: &str, parent: Option<&str>) {
            let old = self.live.get(addr);
            let turn = old.and_then(|r| r["turn"].as_u64()).unwrap_or(0)
                + u64::from(old.is_some_and(|r| r["status"] == "working") && word != "working");
            self.live.insert(addr.into(), json!({"status": word, "parent": parent, "turn": turn}));
        }
        fn tick(&mut self, ms: u64, looking: bool) -> Vec<(String, Alert, bool)> {
            self.now += Duration::from_millis(ms);
            let visible = if looking { HashSet::from(["remote/t1".into()]) } else { HashSet::new() };
            let result = self.alerts.poll(&self.model, &self.live, &visible, self.now);
            for (addr, rec) in &self.live {
                self.model.set_word(addr, rec["status"].as_str().map(str::to_owned), None, looking);
                self.model.set_turn(addr, rec["turn"].as_u64().unwrap_or(0), None);
            }
            result
        }
    }
    #[test]
    fn nested_workers_hold_until_team_settles_and_deliver_once_with_current_attention() {
        let mut t = Team::new();
        t.set("remote/t2", "idle", Some("remote/t1"));
        t.set("remote/t3", "working", Some("remote/t2"));
        t.set("remote/t1", "done", None);
        assert!(t.tick(0, true).is_empty());
        assert!(t.tick(10_000, false).is_empty());
        t.set("remote/t3", "done", Some("remote/t2"));
        assert!(t.tick(0, false).is_empty());
        assert!(t.tick(2999, false).is_empty());
        t.set("remote/t3", "working", Some("remote/t2"));
        assert!(t.tick(1, false).is_empty());
        t.set("remote/t3", "idle", Some("remote/t2"));
        assert!(t.tick(1, false).is_empty());
        assert_eq!(t.tick(3000, true), [("remote/t1".into(), Alert::Done, true)]);
        assert!(t.tick(5000, false).is_empty());
    }
    #[test]
    fn blocked_idle_and_unrelated_workers_do_not_delay_ordinary_alerts() {
        let mut t = Team::new();
        t.set("remote/t2", "blocked", Some("remote/t1"));
        t.set("remote/t3", "idle", Some("remote/t2"));
        t.set("other/t1", "working", None);
        t.set("other/t2", "working", Some("other/t1"));
        t.set("remote/t1", "done", None);
        assert_eq!(t.tick(0, false), [("remote/t1".into(), Alert::Done, false)]);
    }
    #[test]
    fn restarting_parent_cancels_and_next_completion_is_judged_again() {
        let mut t = Team::new();
        t.set("remote/t2", "working", Some("remote/t1"));
        t.set("remote/t1", "done", None);
        assert!(t.tick(0, false).is_empty());
        t.set("remote/t2", "done", Some("remote/t1"));
        assert!(t.tick(0, false).is_empty());
        t.set("remote/t1", "working", None);
        assert!(t.tick(2900, false).is_empty());
        assert!(t.tick(5000, false).is_empty());
        t.set("remote/t1", "idle", None);
        assert_eq!(t.tick(0, false), [("remote/t1".into(), Alert::Done, false)]);
    }
    #[test]
    fn held_alert_uses_attention_at_delivery_and_blocked_is_immediate() {
        let mut t = Team::new();
        t.set("remote/t2", "working", Some("remote/t1"));
        t.set("remote/t1", "done", None);
        assert!(t.tick(0, true).is_empty());
        t.set("remote/t2", "blocked", Some("remote/t1"));
        assert!(t.tick(0, true).is_empty());
        assert_eq!(t.tick(3000, false), [("remote/t1".into(), Alert::Done, false)]);
        t.set("remote/t1", "working", None);
        t.tick(0, false);
        t.set("remote/t2", "working", Some("remote/t1"));
        t.set("remote/t1", "done", None);
        assert!(t.tick(0, false).is_empty());
        t.set("remote/t1", "blocked", None);
        assert_eq!(t.tick(0, true), [("remote/t1".into(), Alert::Blocked, true)]);
        assert!(t.tick(5000, false).is_empty());
    }
    #[test]
    fn disappearing_or_ineligible_parent_cannot_leave_a_stale_completion() {
        for removal in ["terminal", "pane", "child", "status"] {
            let mut t = Team::new();
            t.set("remote/t2", "working", Some("remote/t1"));
            t.set("remote/t1", "done", None);
            assert!(t.tick(0, false).is_empty());
            match removal {
                "terminal" => { t.live.remove("remote/t1"); }
                "pane" => t.model.spaces.clear(),
                "child" => t.set("remote/t1", "done", Some("other/t1")),
                _ => { t.live.get_mut("remote/t1").unwrap()["status"] = Value::Null; }
            }
            assert!(t.tick(5000, false).is_empty());
            t.set("remote/t2", "idle", Some("remote/t1"));
            assert!(t.tick(5000, false).is_empty());
        }
    }
    #[test]
    fn coalesced_nested_worker_turn_restarts_quiet() {
        let mut t = Team::new();
        t.set("remote/t2", "idle", Some("remote/t1"));
        t.set("remote/t3", "working", Some("remote/t2"));
        t.set("remote/t1", "done", None);
        assert!(t.tick(0, false).is_empty());
        t.set("remote/t3", "done", Some("remote/t2"));
        assert!(t.tick(0, false).is_empty());
        t.set("remote/t3", "working", Some("remote/t2"));
        t.set("remote/t3", "done", Some("remote/t2"));
        assert!(t.tick(3000, false).is_empty());
        assert!(t.tick(2999, false).is_empty());
        assert_eq!(t.tick(1, false), [("remote/t1".into(), Alert::Done, false)]);
    }
    #[test]
    fn coalesced_parent_turn_cancels_and_rejudges_without_affecting_unheld_alerts() {
        for child_working in [false, true] {
            let mut t = Team::new();
            t.set("remote/t2", "working", Some("remote/t1"));
            t.set("remote/t1", "done", None);
            assert!(t.tick(0, false).is_empty());
            if !child_working { t.set("remote/t2", "done", Some("remote/t1")); }
            assert!(t.tick(0, false).is_empty());
            t.set("remote/t1", "working", None);
            t.set("remote/t1", "done", None);
            assert!(t.tick(3000, true).is_empty());
            if child_working {
                t.set("remote/t2", "done", Some("remote/t1"));
                assert!(t.tick(0, false).is_empty());
                assert_eq!(t.tick(3000, false), [("remote/t1".into(), Alert::Done, false)]);
            }
            assert!(t.tick(5000, false).is_empty());
            t.set("remote/t1", "working", None);
            t.set("remote/t1", "done", None);
            assert!(t.tick(0, false).is_empty());
        }
    }
    #[test]
    fn delayed_original_completion_turn_does_not_cancel_hold() {
        let mut t = Team::new();
        t.set("remote/t2", "working", Some("remote/t1"));
        t.set("remote/t1", "done", None);
        t.live.get_mut("remote/t1").unwrap()["turn"] = json!(0);
        assert!(t.tick(0, true).is_empty());
        t.live.get_mut("remote/t1").unwrap()["turn"] = json!(1);
        assert!(t.tick(1000, true).is_empty());
        t.set("remote/t2", "done", Some("remote/t1"));
        assert!(t.tick(0, true).is_empty());
        assert_eq!(t.tick(3000, true), [("remote/t1".into(), Alert::Done, true)]);
    }
}
