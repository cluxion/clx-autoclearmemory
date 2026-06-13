use serde::{Deserialize, Serialize};

/// Retention score inputs (Recall-centric).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetentionInput {
    /// Days since last recall.
    pub days_since_recall: f64,
    /// Retrieval count (explicit + implicit + reflection).
    pub retrieval_count: f64,
    /// Importance 0.0..1.0
    pub importance: f64,
    /// Frequency secondary factor 0.0..1.0
    pub frequency: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetentionResult {
    pub retention: f64,
    pub stability: f64,
    pub boost: f64,
}

/// R = e^{-t/S} * (1 + 0.45*N_r + 0.30*I + 0.25*F), S = ln(1 + N_r)
pub fn compute_retention(input: &RetentionInput) -> RetentionResult {
    let n_r = input.retrieval_count.max(0.0);
    let stability = (1.0 + n_r).ln().max(0.001);
    let t = input.days_since_recall.max(0.0);
    let decay = (-t / stability).exp();
    let boost = 1.0 + 0.45 * n_r + 0.30 * input.importance.clamp(0.0, 1.0) + 0.25 * input.frequency.clamp(0.0, 1.0);
    let retention = (decay * boost).clamp(0.0, 10.0);
    RetentionResult {
        retention,
        stability,
        boost,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn higher_recall_increases_retention() {
        let low = compute_retention(&RetentionInput {
            days_since_recall: 10.0,
            retrieval_count: 0.0,
            importance: 0.5,
            frequency: 0.2,
        });
        let high = compute_retention(&RetentionInput {
            days_since_recall: 10.0,
            retrieval_count: 5.0,
            importance: 0.5,
            frequency: 0.2,
        });
        assert!(high.retention > low.retention);
    }
}