from flask import Flask, request, jsonify
import os
import threading
import time
import subprocess
import logging
import argparse

# Set up argument parsing
parser = argparse.ArgumentParser(description="Flask server with Git webhook integration.")
parser.add_argument('--git-repo-dir', required=True, help="Directory where the Git repository is located. Enter the path where the repo will be added to (parent folder where the repo will be downloaded)")
parser.add_argument('--port', type=int, default=5123, help="Port on which the Flask app will listen. Default is 5123.")

args = parser.parse_args()

# Define the directory where your Git repository is located
GIT_REPO_DIR = args.git_repo_dir

# Define the port on which the Flask app will listen
LISTEN_PORT = args.port

# Flag to indicate if the server should restart
should_restart = False
restart_lock = threading.Lock()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/webhook/<path:subpath>', methods=['POST'])
def webhook(subpath):
    global should_restart

    # Print "Received" when the webhook is hit
    logger.info(f"Received webhook for subpath: {subpath}")
    
    FULL_PATH=GIT_REPO_DIR + subpath
    
    try:
        if os.path.isdir(FULL_PATH):
            logger.info(f"Full path of repo for update: {FULL_PATH}")
            # Perform Git operations
            subprocess.run(["git", "fetch"], cwd=FULL_PATH, check=True)
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=FULL_PATH, check=True)

            # Set the restart flag
            with restart_lock:
                should_restart = True

            return jsonify({"status": "success", "message": "Git pull successfull"}), 200
        else:
            logger.critical(f"Directory doesn't exist: {FULL_PATH}")
            return jsonify({"status": "error", "message": "repo doesnt exist"}), 500
            
        
    except subprocess.CalledProcessError as e:
        # Log the error
        error_message = f"Git pull failed: {e.stderr}"
        logger.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

# Restarts server 
def restart_server():
    global should_restart
    while True:
        with restart_lock:
            if should_restart:
                logger.info("Restarting server...")
                # Reset the flag
                should_restart = False
                # Trigger a reload by modifying a file (e.g., app.py)
                with open(__file__, 'a'):
                    os.utime(__file__, None)
        time.sleep(1)

if __name__ == '__main__':
    # Start the restart monitor thread
    threading.Thread(target=restart_server, daemon=True).start()

    # Run the Flask app on the specified port
    logger.info(f"Starting Flask server on port {LISTEN_PORT}...")
    app.run(host='0.0.0.0', port=LISTEN_PORT, use_reloader=True)