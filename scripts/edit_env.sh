#!/bin/bash

# Configuration Paths (updated to match actual project structure)
AGENT_ENV="../Architect_MultiClient_Server/agents/.env"
SERVER_VOSK_ENV="../Architect_MultiClient_Server/server_vosk/.env"

# Current target content
TARGET_FILE=""

# Help message
print_usage() {
    echo "Usage: ./edit_env.sh [TARGET_FLAG] KEY=VALUE [KEY=VALUE ...]"
    echo ""
    echo "Targets:"
    echo "  --agent         Target Agent .env ($AGENT_ENV)"
    echo "  --server-vosk   Target Server Vosk .env ($SERVER_VOSK_ENV)"
    echo "  --stt           Alias for --server-vosk (for backward compatibility)"
    echo ""
    echo "Examples:"
    echo "  ./edit_env.sh --agent LIVEKIT_API_KEY=foo LIVEKIT_API_SECRET=bar"
    echo "  ./edit_env.sh --server-vosk VOSK_MODEL_PATH=/path/to/model"
    echo "  ./edit_env.sh --stt SERVER_PORT=8000"
}

# Function to escape string for sed replacement
escape_sed_replacement() {
    echo "$1" | sed -e 's/[\/&]/\\&/g'
}

# Function to update or add environment variable
update_env_var() {
    local file=$1
    local key=$2
    local value=$3

    # Check if file exists
    if [ ! -f "$file" ]; then
        echo "Creating new .env file: $file"
        mkdir -p "$(dirname "$file")"
        touch "$file"
    fi

    # Check if key exists in file
    if grep -q "^${key}=" "$file"; then
        # Key exists, update it
        # Escape value for use in sed
        local escaped_value
        escaped_value=$(escape_sed_replacement "$value")
        
        # Use sed to replace the line
        # Using | as delimiter to avoid conflict with / in paths
        sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$file"
        echo "Updated $key in $file"
    else
        # Key does not exist, append it
        # Ensure there is a newline at the end of file before appending if file is not empty
        if [ -s "$file" ] && [ "$(tail -c1 "$file" | wc -l)" -eq 0 ]; then
            echo "" >> "$file"
        fi
        echo "${key}=${value}" >> "$file"
        echo "Added $key to $file"
    fi
}

# Check if no arguments provided
if [ $# -eq 0 ]; then
    print_usage
    exit 1
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)
            TARGET_FILE="$AGENT_ENV"
            shift
            ;;
        --server-vosk|--server)
            TARGET_FILE="$SERVER_VOSK_ENV"
            shift
            ;;
        --stt)
            # Backward compatibility: --stt maps to server_vosk
            TARGET_FILE="$SERVER_VOSK_ENV"
            shift
            ;;
        --help|-h)
            print_usage
            exit 0
            ;;
        *=*)
            if [ -z "$TARGET_FILE" ]; then
                echo "Error: No target specified for variable $1"
                echo "Please specify --agent, --server-vosk, or --stt first."
                exit 1
            fi
            
            KEY="${1%%=*}"
            VALUE="${1#*=}"
            
            update_env_var "$TARGET_FILE" "$KEY" "$VALUE"
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            print_usage
            exit 1
            ;;
    esac
done
