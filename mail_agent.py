import os
import base64
import json
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI
from dotenv import load_dotenv

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose"]

LABEL_NAME = "LEAVE_REQUEST"

def get_gmail_service():
  """Authenticates with Gmail and returns a service object."""
  creds = None
  if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
  
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
      creds = flow.run_local_server(port=0)
    with open("token.json", "w") as token:
      token.write(creds.to_json())
  
  try:
    service = build("gmail", "v1", credentials=creds)
    return service
  except HttpError as error:
    print(f"An error occurred while building the service: {error}")
    return None

def get_email_body(parts):
  """Parses the 'parts' of an email to find the plain text body."""
  for part in parts:
    if part['mimeType'] == 'text/plain':
      data = part['body']['data']
      return base64.urlsafe_b64decode(data).decode('utf-8')
    elif 'parts' in part:
      return get_email_body(part['parts'])
  return None

def get_daily_capacity_map(schedule):
    """Helper: Creates a dict showing how many people are out each day."""
    capacity_map = {}
    for event in schedule:
        try:
            start = datetime.strptime(event["start_date"], "%Y-%m-%d")
            end = datetime.strptime(event["end_date"], "%Y-%m-%d")
            delta = end - start
            
            for i in range(delta.days + 1):
                day = start + timedelta(days=i)
                day_str = day.strftime("%Y-%m-%d")
                capacity_map[day_str] = capacity_map.get(day_str, 0) + 1
        except ValueError:
            continue
    return capacity_map

def calculate_overlap_days(requested_dates, event):
    """Helper: Calculates the number of overlapping days."""
    try:
        requested_set = set(requested_dates)
        event_start = datetime.strptime(event["start_date"], "%Y-%m-%d")
        event_end = datetime.strptime(event["end_date"], "%Y-%m-%d")
        
        event_dates = set()
        delta = event_end - event_start
        for i in range(delta.days + 1):
            event_dates.add((event_start + timedelta(days=i)).strftime("%Y-%m-%d"))
            
        overlap = requested_set.intersection(event_dates)
        return len(overlap)
    except ValueError:
        return 0 

# TOOL 2: Check Policy (Upgraded Logic)
def check_team_policy(requested_dates_list):
  """
  It performs two checks:
  1. Capacity Check: Rejects if 3+ people are out on ANY requested day.
  2. Overlap Check: Rejects if the request overlaps 70%+ with ANY single person.
  """
  try:
    with open("vacation_schedule.json", "r") as f:
      schedule_data = json.load(f)
    
    team_schedule = schedule_data.get("team_schedule", [])
    
    if not requested_dates_list:
        return "Status: Error. No dates provided."
        
    # --- Check 1: Team Capacity Rule ---
    print("[Tool 2] Checking team capacity...")
    capacity_map = get_daily_capacity_map(team_schedule)
    
    for day_str in requested_dates_list:
        if capacity_map.get(day_str, 0) >= 3:
            print(f"[Tool 2] Result: HARD CONFLICT (Capacity). Day {day_str} already has {capacity_map.get(day_str)} people out.")
            return (f"Status: Hard Conflict (Capacity). "
                    f"The day {day_str} already has 3 or more people on leave. "
                    f"The team strength would be too low.")

    # --- Check 2: 70% Overlap Rule ---
    print("[Tool 2] Checking 70% overlap rule...")
    total_requested_days = len(requested_dates_list)
    
    for event in team_schedule:
        overlap_days = calculate_overlap_days(requested_dates_list, event)
        
        if overlap_days > 0:
            overlap_percentage = (overlap_days / total_requested_days) * 100
            
            if overlap_percentage >= 70:
                print(f"[Tool 2] Result: HARD CONFLICT (Overlap). {overlap_percentage:.0f}% overlap with {event['person']}.")
                return (f"Status: Hard Conflict (Overlap). "
                        f"The request overlaps by {overlap_percentage:.0f}% "
                        f"with {event['person']}'s leave "
                        f"from {event['start_date']} to {event['end_date']}.")

    # If both checks pass 
    print("[Tool 2] Result: NO CONFLICT. Approved.")
    return "Status: Approved. No conflicts found."
  
  except Exception as e:
    print(f"[Tool 2] Error: {e}")
    return f"Status: Error processing policy. {e}"

# Tool 4: Add Leave to Schedule
def add_leave_to_schedule(person, start_date, end_date):
  """
   Adds an approved leave request to the vacation_schedule.json file.
  """
  try:
    
    with open("vacation_schedule.json", "r") as f:
      schedule_data = json.load(f)
    
    team_schedule = schedule_data.get("team_schedule", [])
    
    
    new_event = {
        "person": person,
        "start_date": start_date,
        "end_date": end_date
    }
    
    
    team_schedule.append(new_event)
    schedule_data["team_schedule"] = team_schedule
    
    
    with open("vacation_schedule.json", "w") as f:
      json.dump(schedule_data, f, indent=2)
      
    print(f"[Tool 4] Schedule updated. Added {person} from {start_date} to {end_date}.")
    return "Status: Successfully added to schedule."

  except Exception as e:
    print(f"[Tool 4] Error: {e}")
    return f"Status: Error adding to schedule. {e}"

# TOOL 3: Create Draft Reply
def create_email_draft(thread_id, to_sender, subject, reply_body):
  """
  Creates a draft reply in the specified email thread.
  """
  try:
    service = get_gmail_service()
    mime_message = EmailMessage()
    mime_message.set_content(reply_body)
    mime_message["To"] = to_sender
    
    if not subject.lower().startswith("re:"):
        mime_message["Subject"] = f"Re: {subject}"
    else:
        mime_message["Subject"] = subject
    
    encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
    create_request = {'message': {'raw': encoded_message, 'threadId': thread_id}}
    
    draft = service.users().drafts().create(userId='me', body=create_request).execute()
    print(f"\n[Tool 3] Successfully created draft. Draft ID: {draft['id']}")
    return f"Status: Draft created successfully. ID: {draft['id']}"
  except Exception as e:
    print(f"[Tool 3] An error occurred: {e}")
    return f"Status: Error creating draft. {e}"

# TOOL 1: Get Emails 
def get_leave_requests():
  """
  Fetches all unread emails with the 'LEAVE_REQUEST' label.
  Returns a list of email objects.
  """
  service = get_gmail_service()
  if not service: return []
  try:
    results = service.users().messages().list(userId="me", q=f"label:{LABEL_NAME} is:unread").execute()
    messages = results.get("messages", [])
    if not messages:
      print("[Tool 1] No unread leave requests found.")
      return []
    
   
    messages.reverse()
    
    print(f"[Tool 1] Found {len(messages)} leave request(s)... (Processing oldest first)")
    
    processed_emails = []
    for msg in messages:
      full_msg = service.users().messages().get(userId="me", id=msg['id'], format="full").execute()
      payload = full_msg['payload']
      headers = payload['headers']
      sender = next(h['value'] for h in headers if h['name'] == 'From')
      subject = next(h['value'] for h in headers if h['name'] == 'Subject')
      body = ""
      if 'parts' in payload:
        body = get_email_body(payload['parts'])
      else:
        body_data = payload['body']['data']
        body = base64.urlsafe_b64decode(body_data).decode('utf-8')
      
      email_data = {
          "msg_id": msg['id'],
          "thread_id": full_msg['threadId'],
          "sender": sender,
          "subject": subject,
          "body": body
      }
      processed_emails.append(email_data)
    return json.dumps(processed_emails)
  except HttpError as error:
    print(f"[Tool 1] An error occurred: {error}")
    return json.dumps([])

load_dotenv()

try:
    client = OpenAI()
except Exception as e:
    print(f"Error initializing OpenAI client: {e}")
    print("Make sure you have set the OPENAI_API_KEY in your .env file.")
    exit()

#tool menu
tools_list = [
    {
        "type": "function",
        "function": {
            "name": "get_leave_requests",
            "description": "Fetches all unread emails with the 'LEAVE_REQUEST' label from Gmail.",
            "parameters": {}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_team_policy",
            "description": "Checks the team's vacation schedule for conflicts using two rules: Capacity (3+ people) and Overlap (70%+).",
            "parameters": {
                "type": "object",
                "properties": {
                    "requested_dates_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of ALL dates to check, formatted as YYYY-MM-DD. (e.g., 'Dec 10-12' is ['2025-12-10', '2025-12-11', '2025-12-12'])."
                    }
                },
                "required": ["requested_dates_list"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_leave_to_schedule",
            "description": "Adds an approved leave request to the vacation_schedule.json file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "The name of the person taking leave. Extract just the name (e.g., 'John Doe') from the sender string."},
                    "start_date": {"type": "string", "description": "The start date of the leave, formatted as YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "The end date of the leave, formatted as YYYY-MM-DD."}
                },
                "required": ["person", "start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_email_draft",
            "description": "Creates a draft reply to an email thread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "The ID of the email thread to reply to."},
                    "to_sender": {"type": "string", "description": "The 'From' header of the original email (e.g., 'John Doe <john.doe@example.com>')."},
                    "subject": {"type": "string", "description": "The original subject line of the email."},
                    "reply_body": {"type": "string", "description": "The full text content of the reply email."}
                },
                "required": ["thread_id", "to_sender", "subject", "reply_body"]
            }
        }
    }
]

# the Assistant
assistant = client.beta.assistants.create(
  name="Gmail Leave Manager",
  
  instructions=(
      "You are an expert HR assistant. Your job is to process leave requests from Gmail using a strict First-Come, First-Served (FCFS) model. "
      "1. First, call `get_leave_requests` to find new emails. This tool returns emails oldest-first. "
      "2. If there are no requests, stop. "
      "3. If there are requests, process them ONE BY ONE in the exact FCFS order provided. "
      "4. For each email, YOU MUST extract the requested dates. Be smart, if the email says 'Dec 10-12', extract the full list: ['YYYY-12-10', 'YYYY-12-11', 'YYYY-12-12']. "
      "5. Then, call `check_team_policy` with this full list of dates. "
      "6. Based on the policy result, generate a friendly, human-sounding reply. "
      "   - If 'Status: Approved', you MUST FIRST call `add_leave_to_schedule`. Extract the sender's name for 'person', and use the first and last dates for start/end. "
      "   - After the schedule is updated, generate a happy approval reply. "
      "   - If 'Status: Hard Conflict (Capacity)', be professional and apologetic. **Specifically state the date(s) that have a capacity issue (this info will be in the tool's status message).** Then, ask them to suggest alternative dates. "
      "   - If 'Status: Hard Conflict (Overlap)', explain that the request has a major (70%+) overlap with another employee. Be sure to state the details from the tool's message. "
      "   - In all conflict cases, DO NOT call `add_leave_to_schedule`. "
      "7. Finally, call `create_email_draft` with all the correct info to create the reply draft. "
      "8. Report a final summary of all actions taken."
  ),
  

  model="gpt-4o",
  tools=tools_list
)

def run_agent():
    print(f"--- Initializing agent (ID: {assistant.id}) ---")
    
    thread = client.beta.threads.create()
    print(f"--- New conversation thread created (ID: {thread.id}) ---")

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content="Please check for any new leave requests and process them."
    )

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id
    )
    print(f"--- Agent Run started (ID: {run.id}) ---")

    while run.status in ['queued', 'in_progress']:
        time.sleep(1) 
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        print(f"Run status: {run.status}")

        if run.status == 'requires_action':
            print("--- Agent requires action! ---")
            tool_outputs = []
            
            for tool_call in run.required_action.submit_tool_outputs.tool_calls:
                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                print(f"Agent wants to call: {tool_name} with args: {arguments}")

                output = None
                if tool_name == "get_leave_requests":
                    output = get_leave_requests()
                
                elif tool_name == "check_team_policy":
                    dates = arguments.get("requested_dates_list")
                    output = check_team_policy(requested_dates_list=dates)
                
                elif tool_name == "add_leave_to_schedule":
                    output = add_leave_to_schedule(
                        person=arguments.get("person"),
                        start_date=arguments.get("start_date"),
                        end_date=arguments.get("end_date")
                    )

                elif tool_name == "create_email_draft":
                    output = create_email_draft(
                        thread_id=arguments.get("thread_id"),
                        to_sender=arguments.get("to_sender"),
                        subject=arguments.get("subject"),
                        reply_body=arguments.get("reply_body")
                    )
                
                if output is not None:
                    tool_outputs.append({
                        "tool_call_id": tool_call.id,
                        "output": output
                    })
            
            if tool_outputs:
                print(f"--- Submitting tool outputs back to agent... ---")
                run = client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread.id,
                    run_id=run.id,
                    tool_outputs=tool_outputs
                )

        elif run.status == 'completed':
            print("--- Agent Run completed! ---")
            messages = client.beta.threads.messages.list(thread_id=thread.id)
            for msg in messages.data:
                if msg.role == "assistant":
                    print(f"Agent: {msg.content[0].text.value}")
                    break
            break 
        
        elif run.status in ['failed', 'cancelled', 'expired']:
            print(f"--- Agent Run failed or was cancelled. ---")
            print(run.last_error)
            break

if __name__ == "__main__":
    try:
        run_agent()
    finally:
        if 'assistant' in locals():
            print(f"\n--- Deleting assistant {assistant.id} ---")
            client.beta.assistants.delete(assistant.id)
            print("Cleanup complete.")