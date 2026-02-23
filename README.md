# Setup
Ensure postgresql@15 is installed and in your path  
Install dotenv and create a .envrc file following the template of envrc_example.sh (ignore DATABASE_URL for now)  
`python3.10 -m venv venv`  
`source venv/bin/activate`  
`pip install -r requirements.txt`  
Open reset.sh and change the DB_DEV_USER to your postgres user  
`./reset.sh`  
Copy the database link it gives you into your .envrc  
`python -m app.main`