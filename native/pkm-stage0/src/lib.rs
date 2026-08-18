#![deny(unsafe_op_in_unsafe_fn)]

use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Workflow {
    Ai,
    Ielts,
    Weekly,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Cause {
    Schedule,
    Logon,
    Unlock,
    Retry,
    Manual,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Stage0Args {
    pub workflow: Workflow,
    pub cause: Cause,
    pub due_only: bool,
}

impl Workflow {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ai => "ai",
            Self::Ielts => "ielts",
            Self::Weekly => "weekly",
        }
    }
}

impl Cause {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Schedule => "schedule",
            Self::Logon => "logon",
            Self::Unlock => "unlock",
            Self::Retry => "retry",
            Self::Manual => "manual",
        }
    }
}

pub const fn propagated_broker_exit(code: Option<i32>) -> u8 {
    match code {
        Some(code @ (0 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10)) => code as u8,
        _ => 3,
    }
}

pub fn same_release_broker_path(stage0: &Path) -> Result<PathBuf, &'static str> {
    if stage0.file_name().and_then(|value| value.to_str()) != Some("pkm-stage0.exe") {
        return Err("stage0 executable leaf is not canonical");
    }
    let release_dir = stage0
        .parent()
        .ok_or("stage0 executable has no release directory")?;
    Ok(release_dir.join("pkm-broker.exe"))
}

pub fn broker_run_arguments(args: Stage0Args) -> Vec<&'static str> {
    let mut values = vec![
        "run",
        "--workflow",
        args.workflow.as_str(),
        "--cause",
        args.cause.as_str(),
    ];
    if args.due_only {
        values.push("--due-only");
    }
    values
}

pub fn parse_args<I, S>(arguments: I) -> Result<Stage0Args, &'static str>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let values: Vec<String> = arguments
        .into_iter()
        .map(|value| value.as_ref().to_owned())
        .collect();
    let mut workflow = None;
    let mut cause = None;
    let mut due_only = false;
    let mut index = 0;
    while index < values.len() {
        match values[index].as_str() {
            "--workflow" => {
                if workflow.is_some() || index + 1 >= values.len() {
                    return Err("--workflow must appear exactly once");
                }
                workflow = Some(match values[index + 1].as_str() {
                    "ai" => Workflow::Ai,
                    "ielts" => Workflow::Ielts,
                    "weekly" => Workflow::Weekly,
                    _ => return Err("unknown workflow"),
                });
                index += 2;
            }
            "--cause" => {
                if cause.is_some() || index + 1 >= values.len() {
                    return Err("--cause must appear exactly once");
                }
                cause = Some(match values[index + 1].as_str() {
                    "schedule" => Cause::Schedule,
                    "logon" => Cause::Logon,
                    "unlock" => Cause::Unlock,
                    "retry" => Cause::Retry,
                    "manual" => Cause::Manual,
                    _ => return Err("unknown cause"),
                });
                index += 2;
            }
            "--due-only" => {
                if due_only {
                    return Err("--due-only cannot be repeated");
                }
                due_only = true;
                index += 1;
            }
            _ => return Err("unknown argument"),
        }
    }
    Ok(Stage0Args {
        workflow: workflow.ok_or("--workflow is required")?,
        cause: cause.ok_or("--cause is required")?,
        due_only,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_the_frozen_workflow_grammar() {
        assert_eq!(
            parse_args(["--workflow", "ai", "--cause", "schedule", "--due-only",]),
            Ok(Stage0Args {
                workflow: Workflow::Ai,
                cause: Cause::Schedule,
                due_only: true,
            })
        );
        assert!(parse_args(["--workflow", "other", "--cause", "manual"]).is_err());
        assert!(parse_args(["--workflow", "ai", "--cause", "other"]).is_err());
        assert!(parse_args([
            "--workflow",
            "ai",
            "--cause",
            "manual",
            "--path",
            "D:\\vault"
        ])
        .is_err());
        assert!(parse_args(["--workflow", "ai", "--workflow", "weekly"]).is_err());
        assert!(parse_args(["--workflow", "ai"]).is_err());
    }

    #[test]
    fn propagates_only_the_frozen_broker_exit_contract() {
        for code in [0, 2, 3, 4, 5, 6, 7, 8, 10] {
            assert_eq!(propagated_broker_exit(Some(code)), code as u8);
        }
        for code in [-1, 1, 9, 11, 255] {
            assert_eq!(propagated_broker_exit(Some(code)), 3);
        }
        assert_eq!(propagated_broker_exit(None), 3);
    }

    #[test]
    fn resolves_only_the_fixed_same_release_broker_leaf() {
        let stage0 = std::path::Path::new(
            r"D:\personal\obsidian_workflow-runtime\durable\releases\aaaaaaaa\pkm-stage0.exe",
        );
        assert_eq!(
            same_release_broker_path(stage0).unwrap(),
            std::path::PathBuf::from(
                r"D:\personal\obsidian_workflow-runtime\durable\releases\aaaaaaaa\pkm-broker.exe"
            )
        );
        assert!(same_release_broker_path(std::path::Path::new(
            r"D:\personal\obsidian_workflow-runtime\pkm-other.exe"
        ))
        .is_err());
    }

    #[test]
    fn builds_only_the_fixed_broker_run_argv() {
        assert_eq!(
            broker_run_arguments(Stage0Args {
                workflow: Workflow::Weekly,
                cause: Cause::Unlock,
                due_only: true,
            }),
            vec![
                "run",
                "--workflow",
                "weekly",
                "--cause",
                "unlock",
                "--due-only",
            ]
        );
        assert_eq!(
            broker_run_arguments(Stage0Args {
                workflow: Workflow::Ai,
                cause: Cause::Manual,
                due_only: false,
            }),
            vec!["run", "--workflow", "ai", "--cause", "manual"]
        );
    }
}
