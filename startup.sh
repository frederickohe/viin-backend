#!/bin/bash

# Set Python path to include the app directory
export PYTHONPATH="${PYTHONPATH}:/app"

# Check if requirements.txt has changed and reinstall if needed
if [ -f /app/.requirements.hash ]; then
    CURRENT_HASH=$(md5sum /app/requirements.txt | awk '{print $1}')
    PREVIOUS_HASH=$(cat /app/.requirements.hash)
    if [ "$CURRENT_HASH" != "$PREVIOUS_HASH" ]; then
        echo "📦 requirements.txt changed. Reinstalling dependencies..."
        pip install -r /app/requirements.txt --no-cache-dir
        echo $CURRENT_HASH > /app/.requirements.hash
        echo "✓ Dependencies updated"
    fi
else
    echo $(md5sum /app/requirements.txt | awk '{print $1}') > /app/.requirements.hash
fi

# Wait for Postgres to be available (tries for ~60s)
echo "Waiting for PostgreSQL to become available..."
TRIES=0
MAX_TRIES=30
until python - <<'PY' 2>/dev/null
import os,sys
from sqlalchemy import create_engine

# Build database URL from environment variables
db_driver = os.environ.get('DB_DRIVER', 'postgresql+psycopg2')
db_user = os.environ.get('PGUSER', 'viinadmin')
db_password = os.environ.get('PGPASSWORD', 'viin098')
db_host = os.environ.get('PGHOST', 'db')
db_port = os.environ.get('PGPORT', '5432')
db_name = os.environ.get('PGDATABASE', 'viin')

url = f'{db_driver}://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}'

# Fallback to SQLALCHEMY_DATABASE_URL if available - but convert to sync driver for health check
if os.environ.get('SQLALCHEMY_DATABASE_URL'):
	url = os.environ.get('SQLALCHEMY_DATABASE_URL')
	# Replace async drivers with sync equivalents for the health check
	url = url.replace('postgresql+asyncpg', 'postgresql+psycopg2')

if not url:
	print('ERROR: No database URL configured')
	sys.exit(2)

try:
	# Use libpq/psycopg2's connect_timeout (seconds) for a short TCP connect timeout
	create_engine(url, connect_args={'connect_timeout': 5}).connect()
	print('✓ Database is ready')
	sys.exit(0)
except Exception as e:
	print(f'✗ Database not ready: {e}')
	sys.exit(1)
PY
do
	TRIES=$((TRIES+1))
	if [ $TRIES -ge $MAX_TRIES ]; then
		echo "⚠ Database did not become available after $((MAX_TRIES*2)) seconds. Proceeding anyway."
		break
	fi
	echo "  Attempt $TRIES/$MAX_TRIES..."
	sleep 2
done

# Wait for Redis to be available
echo "Waiting for Redis to become available..."
REDIS_TRIES=0
REDIS_MAX_TRIES=15
until python - <<'PY' 2>/dev/null
import os, sys, redis
from redis import Redis

try:
	redis_host = os.environ.get('REDIS_HOST', 'localhost')
	redis_port = int(os.environ.get('REDIS_PORT', 6379))
	redis_password = os.environ.get('REDIS_PASSWORD', '')
	
	r = Redis(host=redis_host, port=redis_port, password=redis_password, decode_responses=True, socket_connect_timeout=3)
	r.ping()
	print('✓ Redis is ready')
	sys.exit(0)
except Exception as e:
	print(f'✗ Redis not ready: {e}')
	sys.exit(1)
PY
do
	REDIS_TRIES=$((REDIS_TRIES+1))
	if [ $REDIS_TRIES -ge $REDIS_MAX_TRIES ]; then
		echo "⚠ Redis did not become available. Proceeding anyway (this may affect caching)."
		break
	fi
	echo "  Attempt $REDIS_TRIES/$REDIS_MAX_TRIES..."
	sleep 1
done

# Ensure Alembic migrations folder exists
mkdir -p alembic/versions

# Check AUTO_MIGRATE setting (default to true)
AUTO_MIGRATE=${AUTO_MIGRATE:-true}

if [ "${AUTO_MIGRATE}" = "true" ]; then
	echo "Running Alembic migrations (AUTO_MIGRATE=true)..."
	
	# If there are no revision files in alembic/versions, create an initial autogenerate
	if [ -z "$(ls -A alembic/versions 2>/dev/null | grep -v __pycache__)" ]; then
		echo "  ℹ No Alembic revisions found — creating initial revision"
		python -m alembic revision --autogenerate -m "initial" 2>&1 | grep -v "INFO" || true
	else
		# Create an autogenerate revision for any model changes
		echo "  ℹ Checking for model changes..."
		TIMESTAMP=$(date -u +%Y%m%d%H%M%S)
		python -m alembic revision --autogenerate -m "autogen_$TIMESTAMP" 2>&1 | grep -v "INFO" || true
		
		# Inspect the most recent file created and remove if empty
		LATEST_FILE=$(ls -t alembic/versions/*.py 2>/dev/null | head -n1)
		if [ -n "$LATEST_FILE" ]; then
			if ! grep -q "op\." "$LATEST_FILE"; then
				echo "  ℹ No DB changes detected — skipping empty migration"
				rm -f "$LATEST_FILE"
			else
				echo "  ✓ Migration created: $(basename $LATEST_FILE)"
			fi
		fi
	fi
else
	echo "Skipping auto-migrations (AUTO_MIGRATE not set to 'true')"
fi

# Apply migrations to the database
echo "Applying Alembic migrations (upgrade head)..."
python -m alembic upgrade head 2>&1 | grep -v "INFO" || true

# Start the application with gunicorn
echo "Starting Viin application..."
exec gunicorn \
	--bind 0.0.0.0:8000 \
	--workers 4 \
	--worker-class uvicorn.workers.UvicornWorker \
	--name viin \
	--error-logfile - \
	--access-logfile - \
	--log-level info \
	--timeout 120 \
	src.main:app

