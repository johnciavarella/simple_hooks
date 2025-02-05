use actix_web::{web, App, HttpResponse, HttpServer, Responder};
use clap::Parser;
use git2::{Repository, ResetType};
use log::{debug, error, info, warn};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::fs::File;
use tokio::sync::Mutex;
use tokio::time::{sleep, Duration};

#[derive(Parser, Debug)]
#[command(
    name = "webhook-server",
    about = "Git webhook server with automatic updates",
    long_about = None
)]
struct Args {
    #[arg(long, help = "Parent directory for Git repositories")]
    git_repo_dir: PathBuf,

    #[arg(long, default_value = "5123", help = "Port for the server")]
    port: u16,

    #[arg(long, help = "Security token for webhook authentication")]
    security_token: Option<String>,

    #[arg(long, help = "Enable debug mode")]
    debug: bool,
}

#[derive(Clone)]
struct ServerConfig {
    git_repo_dir: PathBuf,
    security_token: Option<String>,
    debug: bool,
}

#[derive(Debug, Serialize)]
struct ApiResponse {
    status: String,
    message: String,
}

struct AppState {
    config: ServerConfig,
    should_restart: Arc<Mutex<bool>>,
}

#[derive(Debug, thiserror::Error)]
enum ServerError {
    #[error("Git operation failed: {0}")]
    GitError(#[from] git2::Error),
    #[error("Invalid path: {0}")]
    InvalidPath(String),
    #[error("Repository not found: {0}")]
    RepoNotFound(PathBuf),
    #[error("Unauthorized")]
    Unauthorized,
}

impl actix_web::error::ResponseError for ServerError {
    fn error_response(&self) -> HttpResponse {
        let response = ApiResponse {
            status: "error".to_string(),
            message: self.to_string(),
        };

        match self {
            ServerError::Unauthorized => HttpResponse::Unauthorized().json(response),
            ServerError::RepoNotFound(_) => HttpResponse::NotFound().json(response),
            ServerError::InvalidPath(_) => HttpResponse::BadRequest().json(response),
            _ => HttpResponse::InternalServerError().json(response),
        }
    }
}

async fn update_git_repository(repo_path: &Path) -> Result<(), git2::Error> {
    let repo = Repository::open(repo_path)?;
    
    // Reset any local changes
    let head = repo.head()?.peel_to_commit()?;
    repo.reset(head.as_object(), ResetType::Hard, None)?;
    
    // Clean untracked files
    repo.cleanup_state()?;
    
    // Fetch and merge changes
    let mut remote = repo.find_remote("origin")?;
    remote.fetch(&["master"], None, None)?;
    
    let fetch_head = repo.find_reference("FETCH_HEAD")?;
    let fetch_commit = repo.reference_to_annotated_commit(&fetch_head)?;
    
    let (analysis, _) = repo.merge_analysis(&[&fetch_commit])?;
    
    if analysis.is_fast_forward() {
        let mut reference = repo.find_reference("refs/heads/master")?;
        reference.set_target(fetch_commit.id(), "Fast-forward")?;
        repo.set_head("refs/heads/master")?;
        repo.checkout_head(Some(git2::build::CheckoutBuilder::default().force()))?;
    }
    
    Ok(())
}

fn validate_path(path: &str) -> bool {
    let re = Regex::new(r"^[\w\-_/\\\.]+$").unwrap();
    re.is_match(path)
}

async fn handle_webhook(
    path: web::Path<String>,
    state: web::Data<AppState>,
    req: actix_web::HttpRequest,
) -> Result<impl Responder, ServerError> {
    // Validate security token
    if let Some(ref expected_token) = state.config.security_token {
        let token = req
            .headers()
            .get("X-Security-Token")
            .and_then(|h| h.to_str().ok());

        if token != Some(expected_token) {
            warn!("Unauthorized access attempt");
            return Err(ServerError::Unauthorized);
        }

        if state.config.debug {
            debug!("Received token: {:?}", token);
        }
    }

    let subpath = path.into_inner();
    if !validate_path(&subpath) {
        error!("Invalid repository path: {}", subpath);
        return Err(ServerError::InvalidPath(subpath));
    }

    let repo_path = state.config.git_repo_dir.join(&subpath);
    info!("Processing webhook for: {:?}", repo_path);

    if !repo_path.is_dir() {
        error!("Repository directory not found: {:?}", repo_path);
        return Err(ServerError::RepoNotFound(repo_path));
    }

    update_git_repository(&repo_path).await?;

    // Signal restart
    let mut should_restart = state.should_restart.lock().await;
    *should_restart = true;

    Ok(HttpResponse::Ok().json(ApiResponse {
        status: "success".to_string(),
        message: "Repository updated".to_string(),
    }))
}

async fn monitor_restart(should_restart: Arc<Mutex<bool>>) {
    let trigger_path = PathBuf::from("/tmp/webhook_restart_trigger");
    
    loop {
        let restart = {
            let mut flag = should_restart.lock().await;
            let should = *flag;
            *flag = false;
            should
        };

        if restart {
            info!("Restart signal received");
            if let Ok(_file) = File::create(&trigger_path).await {
                info!("Created restart trigger file");
            }
        }

        sleep(Duration::from_secs(1)).await;
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let args = Args::parse();

    // Initialize logging
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or(
        if args.debug { "debug" } else { "info" },
    ))
    .init();

    let config = ServerConfig {
        git_repo_dir: args.git_repo_dir,
        security_token: args.security_token,
        debug: args.debug,
    };

    let should_restart = Arc::new(Mutex::new(false));
    let should_restart_clone = should_restart.clone();

    // Start restart monitor
    tokio::spawn(async move {
        monitor_restart(should_restart_clone).await;
    });

    let state = web::Data::new(AppState {
        config,
        should_restart,
    });

    info!("Starting server on port {}", args.port);

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .route("/webhook/{path:.*}", web::post().to(handle_webhook))
    })
    .bind(("0.0.0.0", args.port))?
    .run()
    .await
}
