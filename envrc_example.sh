export DATABASE_URL='postgresql://app_user:PASSWORD@localhost/study_sessions'

export DISCORD_BOT_TOKEN="TOKEN" # Not currently being used, not needed currently
export USER_PASSWORD="PASSWORD" # Not currently being used, not needed currently
export ADMIN_PASSWORD="PASSWORD"
export SECRET_KEY=$(openssl rand -base64 32)

export ALGORITHM="HS256"
export ACCESS_TOKEN_EXPIRE_MINUTES="30"