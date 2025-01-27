# Python Webhook Git pull
Simple container written for cloud servers to do a gitpull on webhook basedo on <url>:<port>/webhook/<subpath>

docker run -p 5123:5123 -e GIT_REPO_DIR=<repodir> python-webserver 