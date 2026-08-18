use std::env;
use std::process::ExitCode;

use pkm_broker::{parse_args, Command};
use windows_sys::Win32::Foundation::HANDLE;
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::GetCurrentProcess;

const SAFETY_BLOCKED: u8 = 3;

struct OwnedJob(HANDLE);
// Intentionally no Drop implementation: the OS closes this raw handle after
// process exit. Closing it while the current process is alive would make
// KILL_ON_JOB_CLOSE replace the intended broker exit code.

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

fn main() -> ExitCode {
    let args = match parse_args(env::args().skip(1)) {
        Ok(value) => value,
        Err(error) => {
            eprintln!("pkm-broker: {error}");
            return ExitCode::from(2);
        }
    };
    let _job = match create_kill_on_close_job() {
        Ok(job) => job,
        Err(error) => {
            eprintln!("pkm-broker: safety blocked: {error}");
            return ExitCode::from(SAFETY_BLOCKED);
        }
    };
    let exit = match args.command {
        Command::Shadow => {
            println!(
                "{{\"schema_version\":\"7.3\",\"status\":\"BLOCKED\",\"reason\":\"SHADOW_ENGINE_NOT_IMPLEMENTED\",\"vault_write\":false,\"production_eligible\":false}}"
            );
            ExitCode::from(SAFETY_BLOCKED)
        }
        Command::Reconcile => {
            println!(
                "{{\"schema_version\":\"7.3\",\"status\":\"BLOCKED\",\"reason\":\"RECONCILER_NOT_IMPLEMENTED\",\"vault_write\":false,\"production_eligible\":false}}"
            );
            ExitCode::from(SAFETY_BLOCKED)
        }
        Command::Doctor | Command::Run => {
            println!(
                "{{\"schema_version\":\"7.3\",\"status\":\"BLOCKED\",\"reason\":\"NATIVE_QUALIFICATION_INCOMPLETE\",\"vault_write\":false}}"
            );
            ExitCode::from(SAFETY_BLOCKED)
        }
    };
    exit
}
