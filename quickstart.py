import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# These are the scopes we added in the Cloud Console
# If you change these, you MUST delete token.json
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", 
          "https://www.googleapis.com/auth/gmail.compose"]

def main():
  """
  Shows basic usage of the Gmail API.
  Lists the user's Gmail labels and creates token.json.
  """
  creds = None
  
  # The file token.json stores the user's access and refresh tokens.
  # It is created automatically when the authorization flow completes
  # for the first time.
  if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      # This line runs the 'flow' using your credentials.json
      # It will open the browser for you to log in.
      flow = InstalledAppFlow.from_client_secrets_file(
          "credentials.json", SCOPES
      )
      creds = flow.run_local_server(port=0)
      
    # Save the credentials (the token) for the next run
    with open("token.json", "w") as token:
      token.write(creds.to_json())

  try:
    # Build the Gmail service object
    service = build("gmail", "v1", credentials=creds)

    # Call the Gmail API (Test: List labels)
    results = service.users().labels().list(userId="me").execute()
    labels = results.get("labels", [])

    print("Authentication successful!")
    print("Here are some of your Gmail labels:")
    
    if not labels:
      print("No labels found.")
      return
      
    for label in labels[:10]: # Print first 10
      print(f"- {label['name']}")

  except HttpError as error:
    print(f"An error occurred: {error}")


if __name__ == "__main__":
  main()