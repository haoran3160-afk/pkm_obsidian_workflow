#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Command {
    Doctor,
    Shadow,
    Reconcile,
    Run,
}

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
pub struct BrokerArgs {
    pub command: Command,
    pub workflow: Option<Workflow>,
    pub cause: Option<Cause>,
    pub due_only: bool,
}

pub fn parse_args<I, S>(arguments: I) -> Result<BrokerArgs, &'static str>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let values: Vec<String> = arguments
        .into_iter()
        .map(|value| value.as_ref().to_owned())
        .collect();
    let command = match values.first().map(String::as_str) {
        Some("doctor") => Command::Doctor,
        Some("shadow") => Command::Shadow,
        Some("reconcile") => Command::Reconcile,
        Some("run") => Command::Run,
        _ => return Err("expected doctor, shadow, reconcile, or run"),
    };
    let mut workflow = None;
    let mut cause = None;
    let mut due_only = false;
    let mut index = 1;
    while index < values.len() {
        match values[index].as_str() {
            "--workflow" => {
                if workflow.is_some() || index + 1 >= values.len() {
                    return Err("--workflow must not repeat");
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
                    return Err("--cause must not repeat");
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
                    return Err("--due-only must not repeat");
                }
                due_only = true;
                index += 1;
            }
            _ => return Err("unknown argument"),
        }
    }
    match command {
        Command::Run if workflow.is_none() || cause.is_none() => {
            Err("run requires --workflow and --cause")
        }
        Command::Shadow if workflow.is_none() => Err("shadow requires --workflow"),
        Command::Doctor if workflow.is_some() || cause.is_some() || due_only => {
            Err("doctor accepts no options")
        }
        Command::Reconcile if cause.is_some() || due_only => {
            Err("reconcile accepts only optional --workflow")
        }
        _ => Ok(BrokerArgs {
            command,
            workflow,
            cause,
            due_only,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_accepts_only_enumerated_values_and_no_paths() {
        assert!(parse_args([
            "run",
            "--workflow",
            "ai",
            "--cause",
            "schedule",
            "--due-only"
        ])
        .is_ok());
        assert!(parse_args(["run", "--workflow", "ai"]).is_err());
        assert!(parse_args([
            "run",
            "--workflow",
            "ai",
            "--cause",
            "manual",
            "--vault",
            "D:\\vault"
        ])
        .is_err());
    }
}
