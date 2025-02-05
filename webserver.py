from flask import Flask, request, jsonify
import os
import threading
import time
import git
import logging
import argparse
import re
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

@dataclass
class ServerConfig:
    """Server configuration container"""
    git_repo_dir: Path
    port: int
    security_token: Optional[str]
    debug: bool

    def __post_init__(self):
        # Ensure git_repo_dir is a Path object with trailing slash
        self.git_repo_dir = Path(self.git_repo_dir).resolve() / ''

class GitOperations:
    """Handles all Git-related operations using GitPython"""
    
    @staticmethod
    def update_repository(repo_path: Path) -> None:
        """
        Updates the git repository at the specified path
        Raises git.GitCommandError if any git command fails
        """
        try:
            repo = git.Repo(repo_path)
            
            # Reset any local changes
            repo.head.reset(index=True, working_tree=True)
            
            # Clean untracked files
            repo.git.clean('-fd')
            
            # Pull latest changes
            origin = repo.remotes.origin
            origin.pull()
            
        except git.GitCommandError as e:
            raise git.GitCommandError(e.command, e.status, e.stderr)

class WebhookServer:
    """Main webhook server implementation"""
    
    def __init__(self, config: ServerConfig):
        self.config = config
        self.logger = self._setup_logging()
        self.restart_lock = threading.Lock()
        self.should_restart = False
        self.app = self._create_app()

    def _setup_logging(self) -> logging.Logger:
        """Configure logging with appropriate format and level"""
        logger = logging.getLogger(__name__)
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if self.config.debug else logging.INFO)
        return logger

    def _create_app(self) -> Flask:
        """Create and configure Flask application"""
        app = Flask(__name__)
        
        @app.route('/webhook/<path:subpath>', methods=['POST'])
        def webhook(subpath: str):
            return self._handle_webhook(subpath)
        
        return app

    def _validate_security_token(self, token: Optional[str]) -> bool:
        """Validate the security token from request"""
        if self.config.security_token is None:
            return True
        if self.config.debug:
            self.logger.debug(f"Received token: {token}")
        return token == self.config.security_token

    def _validate_path(self, subpath: str) -> bool:
        """Validate the repository path"""
        return bool(re.match(r"^[\w\-\_\/\\\.]+$", subpath))

    def _handle_webhook(self, subpath: str):
        """Handle incoming webhook requests"""
        # Validate security token
        token = request.headers.get('X-Security-Token')
        if not self._validate_security_token(token):
            self.logger.warning(f"Unauthorized access attempt")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        # Validate and construct repository path
        if not self._validate_path(subpath):
            self.logger.error(f"Invalid repository path: {subpath}")
            return jsonify({"status": "error", "message": "Invalid repository path"}), 400

        repo_path = self.config.git_repo_dir / subpath
        self.logger.info(f"Processing webhook for: {repo_path}")

        # Verify repository exists
        if not repo_path.is_dir():
            self.logger.error(f"Repository directory not found: {repo_path}")
            return jsonify({"status": "error", "message": "Repository not found"}), 404

        try:
            # Update repository
            GitOperations.update_repository(repo_path)
            
            # Signal restart
            with self.restart_lock:
                self.should_restart = True
            
            return jsonify({"status": "success", "message": "Repository updated"}), 200

        except git.GitCommandError as e:
            error_msg = f"Git operation failed: {e.stderr}"
            self.logger.error(error_msg)
            return jsonify({"status": "error", "message": error_msg}), 500

    def _monitor_restart(self):
        """Monitor and handle server restart signals"""
        while True:
            with self.restart_lock:
                if self.should_restart:
                    self.logger.info("Restart signal received")
                    self.should_restart = False
                    # Touch a file in /tmp to trigger Flask's reloader
                    restart_trigger = Path("/tmp/webhook_restart_trigger")
                    restart_trigger.touch()
            time.sleep(1)

    def run(self):
        """Start the webhook server"""
        self.logger.info(f"Starting server on port {self.config.port}")
        threading.Thread(target=self._monitor_restart, daemon=True).start()
        self.app.run(
            host='0.0.0.0',
            port=self.config.port,
            use_reloader=True
        )

def parse_arguments() -> ServerConfig:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Flask server with Git webhook integration",
        usage="%(prog)s --port 8080 --security-token TOKEN --git-repo-dir /var/www/",
        epilog="Example: curl -X POST -H \"X-Security-Token: TOKEN\" http://localhost:8080/webhook/site"
    )
    
    parser.add_argument(
        '--git-repo-dir',
        required=True,
        help="Parent directory for Git repositories"
    )
    parser.add_argument(
        '--port',
        type=int,
        default=5123,
        help="Port for the Flask server (default: 5123)"
    )
    parser.add_argument(
        '--security-token',
        help="Security token for webhook authentication"
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help="Enable debug mode (Warning: may expose sensitive information)"
    )
    
    args = parser.parse_args()
    return ServerConfig(
        git_repo_dir=args.git_repo_dir,
        port=args.port,
        security_token=args.security_token,
        debug=args.debug
    )

def main():
    """Main entry point"""
    config = parse_arguments()
    server = WebhookServer(config)
    server.run()

if __name__ == '__main__':
    main()
