//! Opt-in native-process regression: all servers are disposable local fixtures.
use super::*;
use crate::McpServerRegistration;
use crate::ResolvedMcpCatalog;
use codex_config::McpServerConfig;
use codex_protocol::protocol::SessionSource;
use codex_protocol::protocol::SubAgentSource;
use serde_json::Value;
use serde_json::json;
use std::path::Path;

fn fixture_server(
    python: &str,
    fixture: &Path,
    log: &Path,
    owner: &str,
    inherit: bool,
) -> McpServerConfig {
    serde_json::from_value(json!({
        "command": python,
        "args": ["-B", fixture, log, owner],
        "enabled_in_subagents": inherit,
        "required": true,
        "startup_timeout_sec": 10
    }))
    .expect("fixture config")
}

fn input(
    home: &Path,
    environment_manager: &Arc<EnvironmentManager>,
    catalog: ResolvedMcpCatalog,
    source: &SessionSource,
) -> McpRuntimeInput {
    let mut config = crate::mcp::tests::test_mcp_config(home.to_path_buf());
    config.mcp_server_catalog = catalog.for_session_source(source);
    let mcp_servers = crate::effective_mcp_servers(&config, None);
    McpRuntimeInput {
        network_policy_metadata: None,
        startup_policy: if matches!(source, SessionSource::SubAgent(_)) {
            McpStartupPolicy::LazyWhenCached
        } else {
            McpStartupPolicy::Eager
        },
        config: Arc::new(config),
        plugins_available: false,
        ready_selected_capability_roots: Vec::new(),
        mcp_servers,
        submit_id: "worker-scope-probe".into(),
        tx_event: None,
        startup_cancellation_token: CancellationToken::new(),
        runtime_context: McpRuntimeContext::new(
            Arc::clone(environment_manager),
            home.to_path_buf(),
        ),
        codex_apps_tools_cache: Default::default(),
        tool_catalog_cache: Default::default(),
        codex_apps_tools_cache_key: codex_connectors::ConnectorRuntimeContextKey::personal(
            None, None,
        ),
        client_mcp_extensions: Default::default(),
        auth: None,
        auth_manager: None,
        elicitation_reviewer: None,
        elicitation_lifecycle: None,
    }
}

async fn whoami(runtime: &McpRuntime, server: &str) -> anyhow::Result<Value> {
    let result = runtime
        .latest_call_tool(
            server,
            "whoami",
            None,
            Some(json!({})),
            None,
            Some(Duration::from_secs(5)),
            true,
        )
        .await?;
    let response = serde_json::to_value(result.result)?;
    Ok(serde_json::from_str(
        response["content"][0]["text"]
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("fixture identity missing"))?,
    )?)
}

async fn assert_gone(python: &str, fixture: &Path, instance: &Value) -> anyhow::Result<()> {
    for _ in 0..20 {
        let status = tokio::process::Command::new(python)
            .arg("-B")
            .arg(fixture)
            .arg("assert-gone")
            .arg(instance["pid"].to_string())
            .arg(
                instance["identity"]
                    .as_str()
                    .ok_or_else(|| anyhow::anyhow!("creation identity missing"))?,
            )
            .status()
            .await?;
        if status.success() {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    anyhow::bail!("disposable fixture process survived shutdown: {instance}")
}

#[tokio::test]
#[ignore = "requires CODEX_MCP_SCOPE_TEST_PYTHON; starts only disposable fixtures"]
async fn worker_churn_does_not_start_parent_only_mcp_or_stop_parent_process() -> anyhow::Result<()>
{
    let python = std::env::var("CODEX_MCP_SCOPE_TEST_PYTHON")?;
    let home = tempfile::tempdir()?;
    let environment_manager = Arc::new(EnvironmentManager::default_for_tests());
    let fixture = home.path().join("worker_scope_server.py");
    std::fs::write(&fixture, include_str!("worker_scope_server.py"))?;
    let log = home.path().join("events.jsonl");
    let heavy = fixture_server(&python, &fixture, &log, "parent", false);
    let mut catalog = ResolvedMcpCatalog::builder();
    catalog.register(McpServerRegistration::from_config(
        "heavy".into(),
        heavy.clone(),
    ));
    let parent = McpRuntime::new(input(
        home.path(),
        &environment_manager,
        catalog.build(),
        &SessionSource::Cli,
    ))
    .await;
    let parent_identity = whoami(&parent, "heavy").await?;
    let outcome = async {
        for generation in 0..6 {
            let owner = format!("worker-{generation}");
            let mut catalog = ResolvedMcpCatalog::builder();
            catalog.register(McpServerRegistration::from_config(
                "heavy".into(),
                heavy.clone(),
            ));
            catalog.register(McpServerRegistration::from_config(
                "light".into(),
                fixture_server(&python, &fixture, &log, &owner, true),
            ));
            let worker = McpRuntime::new(input(
                home.path(),
                &environment_manager,
                catalog.build(),
                &SessionSource::SubAgent(SubAgentSource::Review),
            ))
            .await;
            let worker_outcome = async {
                worker.validate_required_servers().await?;
                anyhow::ensure!(
                    !worker
                        .latest_wait_for_server_ready("heavy", Duration::from_millis(50))
                        .await,
                    "parent-only MCP started in worker"
                );
                anyhow::ensure!(
                    whoami(&worker, "heavy").await.is_err(),
                    "an explicit call must not start an opted-out MCP"
                );
                whoami(&worker, "light").await
            }
            .await;
            worker.shutdown().await;
            let child = worker_outcome?;
            anyhow::ensure!(child["owner"] == owner);
            assert_gone(&python, &fixture, &child).await?;
            anyhow::ensure!(
                whoami(&parent, "heavy").await? == parent_identity,
                "worker teardown replaced or stopped the parent's exact process"
            );
        }
        let events = std::fs::read_to_string(&log)?;
        let starts = events
            .lines()
            .map(serde_json::from_str::<Value>)
            .collect::<Result<Vec<_>, _>>()?;
        anyhow::ensure!(
            starts
                .iter()
                .filter(|e| e["kind"] == "start" && e["owner"] == "parent")
                .count()
                == 1
        );
        anyhow::ensure!(starts.iter().filter(|e| e["kind"] == "start").count() == 7);
        println!(
            "MCP_SCOPE_PROCESS_EVIDENCE={}",
            serde_json::to_string(&starts)?
        );
        Ok::<_, anyhow::Error>(())
    }
    .await;
    parent.shutdown().await;
    assert_gone(&python, &fixture, &parent_identity).await?;
    outcome
}

#[tokio::test]
#[ignore = "requires CODEX_MCP_SCOPE_TEST_PYTHON; starts only disposable fixtures"]
async fn default_worker_inheritance_still_starts_and_cleans_its_own_process() -> anyhow::Result<()>
{
    let python = std::env::var("CODEX_MCP_SCOPE_TEST_PYTHON")?;
    let home = tempfile::tempdir()?;
    let environment_manager = Arc::new(EnvironmentManager::default_for_tests());
    let fixture = home.path().join("worker_scope_server.py");
    std::fs::write(&fixture, include_str!("worker_scope_server.py"))?;
    let log = home.path().join("events.jsonl");
    let mut catalog = ResolvedMcpCatalog::builder();
    catalog.register(McpServerRegistration::from_config(
        "ordinary".into(),
        fixture_server(&python, &fixture, &log, "parent", true),
    ));
    let parent = McpRuntime::new(input(
        home.path(),
        &environment_manager,
        catalog.build(),
        &SessionSource::Cli,
    ))
    .await;
    let parent_identity = whoami(&parent, "ordinary").await?;
    let outcome = async {
        let mut catalog = ResolvedMcpCatalog::builder();
        // Serialize and reparse with the default flag omitted, as in existing config files.
        let worker_config = fixture_server(&python, &fixture, &log, "worker", true);
        let serialized = serde_json::to_value(worker_config)?;
        anyhow::ensure!(serialized.get("enabled_in_subagents").is_none());
        catalog.register(McpServerRegistration::from_config(
            "ordinary".into(),
            serde_json::from_value(serialized)?,
        ));
        let worker = McpRuntime::new(input(
            home.path(),
            &environment_manager,
            catalog.build(),
            &SessionSource::SubAgent(SubAgentSource::Review),
        ))
        .await;
        let child = whoami(&worker, "ordinary").await;
        worker.shutdown().await;
        let child = child?;
        anyhow::ensure!(child["pid"] != parent_identity["pid"]);
        assert_gone(&python, &fixture, &child).await?;
        anyhow::ensure!(whoami(&parent, "ordinary").await? == parent_identity);
        let mut parent_only = ResolvedMcpCatalog::builder();
        parent_only.register(McpServerRegistration::from_config(
            "ordinary".into(),
            fixture_server(&python, &fixture, &log, "parent", false),
        ));
        // Keep the executor identity stable: replacing it intentionally requires reconnecting.
        parent
            .replace(input(
                home.path(),
                &environment_manager,
                parent_only.build(),
                &SessionSource::Cli,
            ))
            .await;
        anyhow::ensure!(
            whoami(&parent, "ordinary").await? == parent_identity,
            "changing only worker inheritance must retain the parent's live connection"
        );
        println!(
            "MCP_SCOPE_DEFAULT_EVIDENCE={}",
            json!({"parent":parent_identity,"worker":child})
        );
        Ok::<_, anyhow::Error>(())
    }
    .await;
    parent.shutdown().await;
    assert_gone(&python, &fixture, &parent_identity).await?;
    outcome
}
