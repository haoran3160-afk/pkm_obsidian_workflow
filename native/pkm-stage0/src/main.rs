use std::env;
use std::process::{Command, ExitCode};

use pkm_stage0::{
    broker_run_arguments, parse_args, propagated_broker_exit, same_release_broker_path, Stage0Args,
};
use windows_sys::Win32::Foundation::HANDLE;
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::GetCurrentProcess;

const SAFETY_BLOCKED: u8 = 3;
// This binary cannot be activated by an environment variable or command-line
// flag. A later content-addressed release may change this constant only after
// signed PromotionLedger, AppContainer/WFP, and publisher qualification pass.
const NATIVE_QUALIFICATION_COMPLETE: bool = false;

struct OwnedJob(HANDLE);
// Intentionally no Drop implementation: the OS closes this raw handle after
// process exit. Closing it while the current process is alive would make
// KILL_ON_JOB_CLOSE replace the intended stage0 exit code.

fn create_kill_on_close_job() -> Result<OwnedJob, &'static str> {
    // SAFETY: null security/name pointers request an unnamed job with defaults.
    let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if handle.is_null() {
        return Err("CreateJobObjectW failed");
    }
    let job = OwnedJob(handle);
    // SAFETY: zero is the documented baseline for this plain C structure.
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    // SAFETY: pointers reference a live structure for the exact declared size.
    let configured = unsafe {
        SetInformationJobObject(
            job.0,
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    };
    if configured == 0 {
        return Err("SetInformationJobObject failed");
    }
    // SAFETY: GetCurrentProcess returns a valid pseudo-handle for this process.
    let assigned = unsafe { AssignProcessToJobObject(job.0, GetCurrentProcess()) };
    if assigned == 0 {
        return Err("AssignProcessToJobObject failed");
    }
    Ok(job)
}

fn run_qualified_same_release_broker(args: Stage0Args) -> Result<u8, &'static str> {
    let stage0 = env::current_exe().map_err(|_| "current executable path is unavailable")?;
    let release_dir = stage0
        .parent()
        .ok_or("current executable has no release directory")?
        .canonicalize()
        .map_err(|_| "release directory identity is unavailable")?;
    let broker = same_release_broker_path(&stage0)?;
    if !broker.is_file() {
        return Err("same-release broker is missing");
    }
    let broker = broker
        .canonicalize()
        .map_err(|_| "same-release broker identity is unavailable")?;
    if broker.parent() != Some(release_dir.as_path()) {
        return Err("broker escaped the stage0 release directory");
    }
    let status = Command::new(broker)
        .args(broker_run_arguments(args))
        .status()
        .map_err(|_| "same-release broker could not be started")?;
    Ok(propagated_broker_exit(status.code()))
}

fn main() -> ExitCode {
    let args = match parse_args(env::args().skip(1)) {
        Ok(value) => value,
        Err(error) => {
            eprintln!("pkm-stage0: {error}");
            return ExitCode::from(2);
        }
    };
    let _job = match create_kill_on_close_job() {
        Ok(job) => job,
        Err(error) => {
            eprintln!("pkm-stage0: safety blocked: {error}");
            return ExitCode::from(SAFETY_BLOCKED);
        }
    };
    if !NATIVE_QUALIFICATION_COMPLETE {
        eprintln!(
            "pkm-stage0: safety blocked: signed PromotionLedger/native broker qualification missing"
        );
        return ExitCode::from(SAFETY_BLOCKED);
    }
    match run_qualified_same_release_broker(args) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("pkm-stage0: safety blocked: {error}");
            ExitCode::from(SAFETY_BLOCKED)
        }
    }
}
