use serde::{Deserialize, Serialize};

use crate::scoring::{RetentionInput, compute_retention};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryTier {
    Hot,
    WarmEpisodic,
    WarmSemantic,
    WarmProcedural,
    Cold,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TierDecision {
    pub tier: MemoryTier,
    pub action: String,
    pub retention: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TierInput {
    pub days_since_recall: f64,
    pub retrieval_count: f64,
    pub importance: f64,
    pub frequency: f64,
    #[serde(default)]
    pub is_procedural: bool,
    #[serde(default)]
    pub keep_forever: bool,
}

pub fn decide_tier(input: &TierInput) -> TierDecision {
    if input.keep_forever {
        return TierDecision {
            tier: MemoryTier::WarmSemantic,
            action: "keep_forever_tag".into(),
            retention: 1.0,
        };
    }
    let scored = compute_retention(&RetentionInput {
        days_since_recall: input.days_since_recall,
        retrieval_count: input.retrieval_count,
        importance: input.importance,
        frequency: input.frequency,
    });
    let r = scored.retention;
    let n_r = input.retrieval_count;
    let days = input.days_since_recall;

    if days <= 7.0 && n_r > 0.0 {
        return TierDecision {
            tier: MemoryTier::Hot,
            action: "inject_to_prompt".into(),
            retention: r,
        };
    }
    if input.is_procedural && n_r >= 3.0 {
        return TierDecision {
            tier: MemoryTier::WarmProcedural,
            action: "keep_procedural".into(),
            retention: r,
        };
    }
    if r >= 0.80 {
        return TierDecision {
            tier: MemoryTier::WarmSemantic,
            action: "long_term_semantic".into(),
            retention: r,
        };
    }
    if r >= 0.65 && days <= 30.0 {
        return TierDecision {
            tier: MemoryTier::WarmEpisodic,
            action: "spaced_repetition".into(),
            retention: r,
        };
    }
    if r < 0.40 || days >= 180.0 {
        return TierDecision {
            tier: MemoryTier::Cold,
            action: "archive_on_demand".into(),
            retention: r,
        };
    }
    TierDecision {
        tier: MemoryTier::WarmEpisodic,
        action: "maintain_warm".into(),
        retention: r,
    }
}