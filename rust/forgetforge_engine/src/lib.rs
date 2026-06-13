//! Recall-centric scoring and tier engine, semantics-identical to the
//! Python fallback in `forgetforge.rust_bridge`.

pub mod scoring;
pub mod tier;

pub use scoring::{compute_retention, RetentionInput, RetentionResult};
pub use tier::{decide_tier, MemoryTier, TierDecision, TierInput};

use serde_json::Value;

pub fn run_command(command: &str, payload: &Value) -> Result<Value, String> {
    match command {
        "score" => {
            let input: RetentionInput =
                serde_json::from_value(payload.clone()).map_err(|e| e.to_string())?;
            serde_json::to_value(compute_retention(&input)).map_err(|e| e.to_string())
        }
        "tier" => {
            let input: TierInput =
                serde_json::from_value(payload.clone()).map_err(|e| e.to_string())?;
            serde_json::to_value(decide_tier(&input)).map_err(|e| e.to_string())
        }
        // One call scores the whole table: amortizes the JSON boundary that
        // dominates per-row invocations (pruner hot path).
        "tier-batch" => {
            let items = payload
                .get("items")
                .and_then(Value::as_array)
                .ok_or_else(|| "missing required field: items".to_string())?;
            let mut decisions = Vec::with_capacity(items.len());
            for item in items {
                let input: TierInput =
                    serde_json::from_value(item.clone()).map_err(|e| e.to_string())?;
                decisions.push(serde_json::to_value(decide_tier(&input)).map_err(|e| e.to_string())?);
            }
            Ok(serde_json::json!({ "decisions": decisions }))
        }
        other => Err(format!("unknown command: {other}")),
    }
}

#[cfg(feature = "python")]
mod python_module {
    use pyo3::exceptions::PyRuntimeError;
    use pyo3::prelude::*;

    #[pyfunction]
    fn run(command: &str, payload_json: &str) -> PyResult<String> {
        let payload: serde_json::Value = serde_json::from_str(payload_json)
            .map_err(|err| PyRuntimeError::new_err(err.to_string()))?;
        let result = super::run_command(command, &payload)
            .map_err(PyRuntimeError::new_err)?;
        serde_json::to_string(&serde_json::json!({"ok": true, "result": result}))
            .map_err(|err| PyRuntimeError::new_err(err.to_string()))
    }

    #[pymodule]
    fn forgetforge_engine_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add_function(wrap_pyfunction!(run, module)?)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn score_command_round_trips() {
        let payload = json!({
            "days_since_recall": 3.0,
            "retrieval_count": 2.0,
            "importance": 0.5,
            "frequency": 0.2,
        });
        let result = run_command("score", &payload).expect("score");
        assert!(result["retention"].as_f64().unwrap() > 0.0);
    }

    #[test]
    fn tier_command_round_trips() {
        let payload = json!({
            "days_since_recall": 1.0,
            "retrieval_count": 4.0,
            "importance": 0.9,
            "frequency": 0.8,
            "is_procedural": false,
            "keep_forever": false,
        });
        let result = run_command("tier", &payload).expect("tier");
        assert_eq!(result["tier"].as_str().unwrap(), "hot");
    }

    #[test]
    fn unknown_command_is_rejected() {
        assert!(run_command("nope", &json!({})).is_err());
    }

    #[test]
    fn tier_batch_matches_single_calls() {
        let items = json!([
            {"days_since_recall": 1.0, "retrieval_count": 4.0, "importance": 0.9, "frequency": 0.8},
            {"days_since_recall": 200.0, "retrieval_count": 0.0, "importance": 0.1, "frequency": 0.0},
        ]);
        let batch = run_command("tier-batch", &json!({ "items": items })).expect("batch");
        let decisions = batch["decisions"].as_array().unwrap();
        assert_eq!(decisions.len(), 2);
        for (item, decision) in items.as_array().unwrap().iter().zip(decisions) {
            let single = run_command("tier", item).expect("tier");
            assert_eq!(*decision, single);
        }
    }

    #[test]
    fn tier_batch_requires_items() {
        assert!(run_command("tier-batch", &json!({})).is_err());
    }
}
