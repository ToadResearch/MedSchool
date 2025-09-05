use anyhow::{Result, anyhow};
use clap::Parser;
use poem::{
    EndpointExt, Route, Server, delete, get, handler,
    http::StatusCode,
    listener::TcpListener,
    post,
    web::{Data, Json, Path as PoemPath},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fs,
    path::{Path as FsPath, PathBuf},
    process::Stdio,
    sync::Arc,
    time::Duration,
};
use tokio::{
    process::{Child, Command},
    sync::Mutex,
    time::sleep,
};
use tracing::info;
use uuid::Uuid;

#[derive(Parser, Debug, Clone)]
#[command(
    name = "vm-agent",
    version,
    about = "Host-side microVM controller (Poem)"
)]
struct Args {
    /// TCP port to listen on
    #[arg(long, default_value_t = 40051)]
    port: u16,

    /// Directory for base images / overlays / seed
    #[arg(long, default_value = ".vm")]
    state: PathBuf,

    /// Force guest arch: auto|x86_64|aarch64 (defaults to host arch)
    #[arg(long, default_value = "auto")]
    arch: String,

    /// Machine type: auto|microvm|virt|q35
    #[arg(long, default_value = "auto")]
    machine: String,

    /// Accelerator: auto|hvf|kvm|whpx|tcg
    #[arg(long, default_value = "auto")]
    accel: String,

    /// Optional direct-kernel boot (x86_64 microvm)
    #[arg(long)]
    kernel: Option<PathBuf>,
    #[arg(long)]
    initrd: Option<PathBuf>,
}

#[derive(Clone)]
struct AppState {
    args: Args,
    procs: Arc<Mutex<HashMap<String, ChildMeta>>>,
}

struct ChildMeta {
    child: Child,
    overlay_path: PathBuf,
    ttl_task: Option<tokio::task::JoinHandle<()>>,
    _log_path: PathBuf,
}

#[derive(Deserialize)]
struct CreateReq {
    #[serde(default)]
    ttl_secs: Option<u64>,
    #[serde(default)]
    mem_mb: Option<u64>,
    #[serde(default)]
    cpus: Option<u8>,
    /// Optional: name of base image to use under state/base/
    #[serde(default)]
    base_image: Option<String>,
}

#[derive(Serialize)]
struct CreateResp {
    id: String,
    ssh_port: u16,
    pid: Option<u32>,
}

#[handler]
async fn healthz() -> &'static str {
    "ok"
}

#[handler]
async fn create_vm(
    Data(state): Data<&AppState>,
    Json(req): Json<CreateReq>,
) -> Result<Json<CreateResp>, poem::Error> {
    let id = Uuid::new_v4().to_string();

    let arch = pick_arch(&state.args.arch);
    let accel = pick_accel(&state.args.accel);
    let machine = pick_machine(&state.args.machine, arch, state.args.kernel.as_ref());

    // Pick qemu binary & default base image
    let (qemu_bin, default_base) = match arch {
        "aarch64" => ("qemu-system-aarch64", "ubuntu-arm64.img"),
        "x86_64" => ("qemu-system-x86_64", "ubuntu-amd64.img"),
        other => {
            return Err(poem::Error::from_string(
                format!("unsupported arch {other}"),
                StatusCode::BAD_REQUEST,
            ));
        }
    };

    let state_dir = &state.args.state;
    let base_dir = state_dir.join("base");
    let run_dir = state_dir.join("run");
    let seed_iso = state_dir.join("seed/seed.iso");

    if !seed_iso.exists() {
        return Err(poem::Error::from_string(
            "seed.iso not found (expected at .vm/seed/seed.iso). Run your startup bootstrap first.",
            StatusCode::PRECONDITION_FAILED,
        ));
    }
    fs::create_dir_all(&run_dir).ok();

    let base_img_name = req.base_image.unwrap_or_else(|| default_base.to_string());
    let base_img = base_dir.join(&base_img_name);
    if !base_img.exists() {
        return Err(poem::Error::from_string(
            format!("base image not found: {}", base_img.display()),
            StatusCode::PRECONDITION_FAILED,
        ));
    }

    // Create per-session overlay
    let overlay = run_dir.join(format!("{id}.qcow2"));
    qemu_img_create_overlay(&base_img, &overlay)
        .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;

    // Allocate SSH port
    let ssh_port = portpicker::pick_unused_port().unwrap_or(10022);

    // Build QEMU command
    let mem_mb = req.mem_mb.unwrap_or(1024);
    let cpus = req.cpus.unwrap_or(2);

    let mut cmd = Command::new(qemu_bin);
    cmd.args(["-display", "none", "-monitor", "none"])
        .arg("-smp")
        .arg(cpus.to_string())
        .arg("-m")
        .arg(mem_mb.to_string())
        // .stdout(Stdio::null())
        // .stderr(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .stdin(Stdio::null());

    cmd.arg("-machine")
        .arg(format!("{},accel={}", machine, accel));

    // user-mode networking + hostfwd for SSH
    cmd.arg("-netdev")
        .arg(format!("user,id=n0,hostfwd=tcp:127.0.0.1:{}-:22", ssh_port));
    let net_dev = if machine == "microvm" {
        "virtio-net-device"
    } else {
        "virtio-net-pci"
    };
    cmd.arg("-device").arg(format!("{},netdev=n0", net_dev));

    // Disk + seed
    if machine == "microvm" {
        // microvm: non-PCI devices
        cmd.arg("-drive").arg(format!(
            "if=none,id=os,file={},format=qcow2",
            overlay.display()
        ));
        cmd.arg("-device").arg("virtio-blk-device,drive=os");
    } else {
        cmd.arg("-drive")
            .arg(format!("if=virtio,file={},format=qcow2", overlay.display()));
    }
    cmd.arg("-cdrom").arg(seed_iso.as_os_str());

    // microvm + x86_64: direct kernel boot
    if machine == "microvm" && arch == "x86_64" {
        let kernel = state.args.kernel.clone().ok_or_else(|| {
            poem::Error::from_string(
                "x86_64 microvm requires --kernel (and usually --initrd). Provide them or use --machine q35.",
                StatusCode::PRECONDITION_FAILED,
            )
        })?;
        cmd.arg("-kernel").arg(kernel);
        if let Some(initrd) = state.args.initrd.as_ref() {
            cmd.arg("-initrd").arg(initrd);
        }
        cmd.arg("-append").arg("console=ttyS0 root=/dev/vda1");
        cmd.arg("-nodefaults")
            .arg("-no-user-config")
            // .arg("-serial")
            .arg("stdio");
    }
    // } else {
    //     cmd.arg("-serial").arg("stdio");
    // }

    cmd.arg("-serial").arg("stdio");

    // Spawn QEMU
    let mut child = cmd.spawn().map_err(|e| {
        poem::Error::from_string(
            format!("failed to spawn qemu: {e:#}"),
            StatusCode::INTERNAL_SERVER_ERROR,
        )
    })?;
    let pid = child.id();

    // TTL (optional)
    let procs_arc = state.procs.clone();
    let overlay_clone = overlay.clone();
    let id_clone = id.clone();
    let ttl_task = if let Some(ttl) = req.ttl_secs {
        Some(tokio::spawn(async move {
            sleep(Duration::from_secs(ttl)).await;
            let _ = kill_session(&id_clone, &procs_arc, Some(&overlay_clone)).await;
        }))
    } else {
        None
    };

    // Record process
    let log_path = run_dir.join(format!("{id}.log"));
    {
        let mut map = state.procs.lock().await;
        map.insert(
            id.clone(),
            ChildMeta {
                child,
                overlay_path: overlay,
                ttl_task,
                _log_path: log_path,
            },
        );
    }

    Ok(Json(CreateResp { id, ssh_port, pid }))
}

#[handler]
async fn delete_vm(
    PoemPath(id): PoemPath<String>,
    Data(state): Data<&AppState>,
) -> poem::Result<()> {
    kill_session(&id, &state.procs, None)
        .await
        .map_err(|e| poem::Error::from_string(e.to_string(), StatusCode::INTERNAL_SERVER_ERROR))?;
    Ok(())
}

/// Kill and remove an entry; also deletes overlay.
async fn kill_session(
    id: &str,
    procs: &Arc<Mutex<HashMap<String, ChildMeta>>>,
    overlay_hint: Option<&FsPath>,
) -> Result<()> {
    let mut map = procs.lock().await;
    if let Some(mut meta) = map.remove(id) {
        if let Some(handle) = meta.ttl_task.take() {
            handle.abort();
        }
        let _ = meta.child.kill().await;
        let overlay = overlay_hint.map(PathBuf::from).unwrap_or(meta.overlay_path);
        let _ = fs::remove_file(&overlay);
        Ok(())
    } else {
        Err(anyhow!("session {} not found", id))
    }
}

fn pick_arch(arg: &str) -> &'static str {
    match arg {
        "auto" => {
            if cfg!(target_arch = "aarch64") {
                "aarch64"
            } else {
                "x86_64"
            }
        }
        "aarch64" => "aarch64",
        "x86_64" => "x86_64",
        _ => "x86_64",
    }
}

fn pick_accel(arg: &str) -> &'static str {
    let low = arg.to_ascii_lowercase();
    if low != "auto" {
        return match low.as_str() {
            "hvf" => "hvf",
            "kvm" => "kvm",
            "whpx" => "whpx",
            "tcg" => "tcg",
            _ => "tcg",
        };
    }
    if cfg!(target_os = "macos") {
        "hvf"
    } else if cfg!(target_os = "linux") {
        if FsPath::new("/dev/kvm").exists() {
            "kvm"
        } else {
            "tcg"
        }
    } else if cfg!(target_os = "windows") {
        "whpx"
    } else {
        "tcg"
    }
}

fn pick_machine(arg: &str, arch: &str, kernel: Option<&PathBuf>) -> String {
    if arg != "auto" {
        return arg.to_string();
    }
    match arch {
        "aarch64" => "virt".into(),
        "x86_64" => {
            if kernel.is_some() {
                "microvm".into()
            } else {
                "q35".into()
            }
        }
        _ => "q35".into(),
    }
}

fn qemu_img_create_overlay(base: &FsPath, overlay: &FsPath) -> Result<()> {
    // Ensure overlay directory exists
    if let Some(dir) = overlay.parent() {
        fs::create_dir_all(dir)?;
    }

    // If an old overlay exists (possibly empty/corrupt), remove it first
    if overlay.exists() {
        fs::remove_file(overlay)?;
    }

    // Canonicalize base so backing path stored in qcow2 is absolute
    let abs_base = base.canonicalize()?;

    // Detect the actual format of the base image (qcow2/raw/…)
    let base_fmt = detect_image_format(&abs_base).unwrap_or_else(|_| "qcow2".to_string());

    // Create the overlay
    let status = std::process::Command::new("qemu-img")
        .args(["create", "-f", "qcow2", "-b"])
        .arg(abs_base.as_os_str())
        .args(["-F", &base_fmt])
        .arg(overlay.as_os_str())
        .status()?;

    if !status.success() {
        return Err(anyhow::anyhow!(
            "qemu-img create failed with status {:?}",
            status.code()
        ));
    }
    Ok(())
}

// Very small, dependency-free format detector using `qemu-img info` text output.
fn detect_image_format(img: &FsPath) -> Result<String> {
    let out = std::process::Command::new("qemu-img")
        .args(["info"])
        .arg(img.as_os_str())
        .output()?;
    if !out.status.success() {
        return Err(anyhow::anyhow!(
            "qemu-img info failed ({:?})",
            out.status.code()
        ));
    }
    let s = String::from_utf8_lossy(&out.stdout);
    for line in s.lines() {
        if let Some(rest) = line.trim().strip_prefix("file format:") {
            return Ok(rest.trim().to_string());
        }
    }
    Err(anyhow::anyhow!(
        "could not parse base image format from qemu-img info"
    ))
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt().with_env_filter("info").init();

    let args = Args::parse();
    fs::create_dir_all(args.state.join("run")).ok();

    let state = AppState {
        args: args.clone(),
        procs: Arc::new(Mutex::new(HashMap::new())),
    };

    let app = Route::new()
        .at("/healthz", get(healthz))
        .at("/sessions", post(create_vm))
        .at("/sessions/:id", delete(delete_vm))
        .at(
            "/",
            poem::endpoint::make_sync(|_| {
                poem::Response::builder()
                    .status(StatusCode::OK)
                    .content_type("text/plain")
                    .body("vm-agent (Poem)\nPOST /sessions, DELETE /sessions/:id, GET /healthz\n")
            }),
        )
        .data(state.clone()); // ⟵ last

    let addr = format!("0.0.0.0:{}", args.port);
    info!("vm-agent listening on {}", addr);
    Server::new(TcpListener::bind(addr)).run(app).await?;
    Ok(())
}
