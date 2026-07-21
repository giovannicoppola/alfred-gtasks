import os
import sys
import json
import re
import datetime
from dateutil.relativedelta import relativedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Scope defines what permissions your script needs. tasks grants read/write access.
# If you change this scope, you must delete the 'token.json' file for re-authorization.
SCOPES = ["https://www.googleapis.com/auth/tasks"] 

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def get_google_service():
    """Authenticates the user and returns the Google Tasks API service object."""
    creds = None
    token_path = os.path.join(SCRIPT_DIR, "token.json")
    credentials_path = os.path.join(SCRIPT_DIR, "credentials.json")
    
    # token.json stores the user's access and refresh tokens after the first run.
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    # If no valid credentials exist, prompt the user to log in via a web browser.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print(f"❌ ERROR: 'credentials.json' not found at {credentials_path}")
                sys.exit(1)
            
            # Validate that credentials.json is an OAuth client ID, not a service account
            try:
                with open(credentials_path, 'r') as f:
                    creds_data = json.load(f)
                    if 'type' in creds_data and creds_data['type'] == 'service_account':
                        print("❌ ERROR: Your credentials.json is a service account key.")
                        print("   For Google Tasks API with OAuth2, you need an OAuth client ID instead.")
                        print("\n   To fix this:")
                        print("   1. Go to https://console.cloud.google.com/")
                        print("   2. Select your project → APIs & Services → Credentials")
                        print("   3. Click 'Create Credentials' → 'OAuth client ID'")
                        print("   4. Choose 'Desktop app' as the application type")
                        print("   5. Download the JSON and save it as 'credentials.json'")
                        sys.exit(1)
            except (json.JSONDecodeError, KeyError):
                # If we can't parse it, let the OAuth flow handle the error
                pass
            
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    credentials_path, SCOPES
                )
                print("🌐 Opening browser for authentication...")
                print("   If you see 'access_denied', check the OAuth consent screen configuration.")
                creds = flow.run_local_server(port=0, open_browser=True)
                
                # Check if we got valid credentials
                if not creds or not creds.valid:
                    print("❌ ERROR: Authentication failed or was cancelled.")
                    print("   Please ensure:")
                    print("   1. You clicked 'Allow' in the browser")
                    print("   2. Your email is added as a test user in OAuth consent screen")
                    print("   3. The Google Tasks API scope is added to your consent screen")
                    sys.exit(1)
                    
            except ValueError as e:
                if "Client secrets must be for a web or installed app" in str(e):
                    print("❌ ERROR: Invalid credentials file type.")
                    print("   Your credentials.json must be an OAuth client ID (Desktop app type),")
                    print("   not a service account key.")
                    print("\n   To fix this:")
                    print("   1. Go to https://console.cloud.google.com/")
                    print("   2. Select your project → APIs & Services → Credentials")
                    print("   3. Click 'Create Credentials' → 'OAuth client ID'")
                    print("   4. Choose 'Desktop app' as the application type")
                    print("   5. Download the JSON and save it as 'credentials.json'")
                    print("   6. Make sure Google Tasks API is enabled in your project")
                    sys.exit(1)
                else:
                    raise
            except Exception as oauth_error:
                error_str = str(oauth_error).lower()
                if "access_denied" in error_str or "403" in error_str or "denied" in error_str:
                    print("\n❌ ERROR: OAuth access denied (403)")
                    print("=" * 60)
                    print("This means Google is blocking the authentication request.")
                    print("\n📋 STEP-BY-STEP FIX:")
                    print("\n1. Go to: https://console.cloud.google.com/apis/credentials/consent")
                    print("   (Or: Your Project → APIs & Services → OAuth consent screen)")
                    print("\n2. If you see 'Configure Consent Screen' button, click it.")
                    print("   If already configured, click 'EDIT APP'")
                    print("\n3. Configure these settings:")
                    print("   • User Type: Choose 'External' (unless you have Google Workspace)")
                    print("   • App name: Enter any name (e.g., 'My Tasks App')")
                    print("   • User support email: Your email")
                    print("   • Developer contact: Your email")
                    print("   • Click 'SAVE AND CONTINUE'")
                    print("\n4. On 'Scopes' page:")
                    print("   • Click 'ADD OR REMOVE SCOPES'")
                    print("   • Search for: 'tasks'")
                    print("   • Check: '.../auth/tasks' (Google Tasks API)")
                    print("   • Click 'UPDATE' then 'SAVE AND CONTINUE'")
                    print("\n5. On 'Test users' page:")
                    print("   • Click '+ ADD USERS'")
                    print("   • Add YOUR Google account email address")
                    print("   • Click 'ADD' then 'SAVE AND CONTINUE'")
                    print("\n6. Review and go back to Dashboard")
                    print("\n7. Verify Google Tasks API is enabled:")
                    print("   • Go to: https://console.cloud.google.com/apis/library")
                    print("   • Search: 'Tasks API'")
                    print("   • Click 'ENABLE' if not already enabled")
                    print("\n8. Wait 1-2 minutes, then try running the script again.")
                    print("=" * 60)
                    sys.exit(1)
                else:
                    print(f"❌ OAuth error: {oauth_error}")
                    print("\nIf you saw 'access_denied' in the browser, follow the steps above.")
                    sys.exit(1)
        
        # Save the credentials for the next run
        with open(token_path, "w") as token:
            token.write(creds.to_json())
    
    # Build and return the Tasks API service
    return build("tasks", "v1", credentials=creds)

def parse_date_from_string(input_string):
    """
    Parses date and time from input string and returns (cleaned_title, due_datetime).
    Supports formats:
    - mm-dd (e.g., 11-23) - next occurrence in future
    - yyyy-mm-dd (e.g., 2025-12-03) - specific date
    - mm-dd:hh:mm (e.g., 11-23:23:30) - date with time
    - Natural language: 'today', 'tomorrow', 'next saturday', etc.
    Returns (cleaned_title, due_datetime) where due_datetime is None if no date found.
    """
    now = datetime.datetime.now()
    cleaned_title = input_string
    due_datetime = None
    time_component = None
    
    # Pattern for mm-dd:hh:mm or mm-dd (with optional time)
    # Time format: :hh:mm directly after the date (24-hour format)
    # Match date, optionally followed by time, then ensure it's followed by space, end, or non-digit/non-colon
    date_time_pattern = r'\b(\d{1,2})-(\d{1,2})(?::(\d{1,2}):(\d{2}))?(?=\s|$|[^\d:])'
    # Pattern for yyyy-mm-dd (with optional time)
    # Time format: :hh:mm directly after the date (24-hour format)
    full_date_pattern = r'\b(\d{4})-(\d{1,2})-(\d{1,2})(?::(\d{1,2}):(\d{2}))?(?=\s|$|[^\d:])'
    
    # Try to match yyyy-mm-dd format first
    full_date_match = re.search(full_date_pattern, input_string)
    if full_date_match:
        year, month, day = int(full_date_match.group(1)), int(full_date_match.group(2)), int(full_date_match.group(3))
        # Check if time groups exist and are not empty strings
        hour_str = full_date_match.group(4)
        minute_str = full_date_match.group(5)
        hour = int(hour_str) if hour_str and hour_str.strip() else None
        minute = int(minute_str) if minute_str and minute_str.strip() else None
        
        try:
            due_datetime = datetime.datetime(year, month, day)
            if hour is not None and minute is not None:
                due_datetime = due_datetime.replace(hour=hour, minute=minute)
            elif hour is not None:
                due_datetime = due_datetime.replace(hour=hour, minute=0)
            
            # Remove the matched pattern from title
            cleaned_title = re.sub(full_date_pattern, '', cleaned_title, count=1).strip()
            # Clean up extra spaces
            cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
            return (cleaned_title, due_datetime)
        except ValueError:
            pass
    
    # Try to match mm-dd:hh:mm or mm-dd format
    date_match = re.search(date_time_pattern, input_string)
    if date_match:
        month, day = int(date_match.group(1)), int(date_match.group(2))
        # Check if time groups exist and are not empty strings
        hour_str = date_match.group(3)
        minute_str = date_match.group(4)
        hour = int(hour_str) if hour_str and hour_str.strip() else None
        minute = int(minute_str) if minute_str and minute_str.strip() else None
        
        # Find next occurrence of this date in the future
        current_year = now.year
        try:
            # Try current year first
            candidate = datetime.datetime(current_year, month, day)
            
            # If time is specified, set it on the candidate for proper comparison
            if hour is not None and minute is not None:
                candidate = candidate.replace(hour=hour, minute=minute)
            elif hour is not None:
                candidate = candidate.replace(hour=hour, minute=0)
            else:
                # No time specified, set to start of day for comparison
                candidate = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Compare with current time (also normalized)
            now_normalized = now.replace(second=0, microsecond=0)
            if candidate < now_normalized:
                # If it's in the past, try next year
                candidate = datetime.datetime(current_year + 1, month, day)
                if hour is not None and minute is not None:
                    candidate = candidate.replace(hour=hour, minute=minute)
                elif hour is not None:
                    candidate = candidate.replace(hour=hour, minute=0)
                else:
                    candidate = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
            
            due_datetime = candidate
            
            # Remove the matched pattern from title
            cleaned_title = re.sub(date_time_pattern, '', cleaned_title, count=1).strip()
            # Clean up extra spaces
            cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
            return (cleaned_title, due_datetime)
        except ValueError:
            pass
    
    # Try natural language dates (today, tomorrow, next saturday, etc.)
    # Check for "today"
    if re.search(r'\btoday\b', input_string, re.IGNORECASE):
        due_datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cleaned_title = re.sub(r'\btoday\b', '', cleaned_title, flags=re.IGNORECASE, count=1).strip()
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
        return (cleaned_title, due_datetime)
    
    # Check for "tomorrow"
    if re.search(r'\btomorrow\b', input_string, re.IGNORECASE):
        due_datetime = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        cleaned_title = re.sub(r'\btomorrow\b', '', cleaned_title, flags=re.IGNORECASE, count=1).strip()
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
        return (cleaned_title, due_datetime)
    
    # Check for "next [weekday]" first (more specific pattern, should be checked first)
    next_weekday_match = re.search(r'\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', input_string, re.IGNORECASE)
    if next_weekday_match:
        weekday_name = next_weekday_match.group(1).lower()
        due_datetime = _get_next_weekday(weekday_name)
        cleaned_title = re.sub(r'\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', '', cleaned_title, flags=re.IGNORECASE, count=1).strip()
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
        return (cleaned_title, due_datetime)
    
    # Check for standalone weekday (e.g., "Monday", "Sunday") - means following weekday
    # Check all weekdays and pick the first one that's not preceded by "next"
    for weekday in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
        pattern = r'\b' + weekday + r'\b'
        match = re.search(pattern, input_string, re.IGNORECASE)
        if match:
            # Check if "next" appears before this match
            match_start = match.start()
            before_text = input_string[:match_start].lower().rstrip()
            # Check if "next" is the word immediately before (allowing for whitespace)
            if not re.search(r'\bnext\s*$', before_text):
                weekday_name = weekday
                due_datetime = _get_next_weekday(weekday_name)
                cleaned_title = re.sub(pattern, '', cleaned_title, flags=re.IGNORECASE, count=1).strip()
                cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
                return (cleaned_title, due_datetime)
    
    # Try "in X days/weeks/months" pattern
    in_days_match = re.search(r'\bin\s+(\d+)\s+(day|days|week|weeks|month|months)\b', input_string, re.IGNORECASE)
    if in_days_match:
        try:
            amount = int(in_days_match.group(1))
            unit = in_days_match.group(2).lower()
            
            if unit in ['day', 'days']:
                due_datetime = (now + datetime.timedelta(days=amount)).replace(hour=0, minute=0, second=0, microsecond=0)
            elif unit in ['week', 'weeks']:
                due_datetime = (now + datetime.timedelta(weeks=amount)).replace(hour=0, minute=0, second=0, microsecond=0)
            elif unit in ['month', 'months']:
                due_datetime = (now + relativedelta(months=amount)).replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                due_datetime = (now + datetime.timedelta(days=amount)).replace(hour=0, minute=0, second=0, microsecond=0)
            
            cleaned_title = re.sub(r'\bin\s+\d+\s+(day|days|week|weeks|month|months)\b', '', cleaned_title, flags=re.IGNORECASE, count=1).strip()
            cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
            return (cleaned_title, due_datetime)
        except Exception:
            pass
    
    # Try "next week" or "next month"
    next_period_match = re.search(r'\bnext\s+(week|month)\b', input_string, re.IGNORECASE)
    if next_period_match:
        try:
            period = next_period_match.group(1).lower()
            if period == 'week':
                due_datetime = (now + datetime.timedelta(weeks=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            elif period == 'month':
                due_datetime = (now + relativedelta(months=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            
            cleaned_title = re.sub(r'\bnext\s+(week|month)\b', '', cleaned_title, flags=re.IGNORECASE, count=1).strip()
            cleaned_title = re.sub(r'\s+', ' ', cleaned_title)
            return (cleaned_title, due_datetime)
        except Exception:
            pass
    
    return (cleaned_title, None)

def _get_next_weekday(weekday_name):
    """Get the next occurrence of a weekday."""
    now = datetime.datetime.now()
    weekday_map = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }
    target_weekday = weekday_map.get(weekday_name.lower())
    if target_weekday is None:
        return (now + datetime.timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    current_weekday = now.weekday()
    days_ahead = target_weekday - current_weekday
    
    # If the target weekday is today or has already passed this week, get next week's occurrence
    if days_ahead <= 0:
        days_ahead += 7
    
    return (now + datetime.timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)

def add_task(task_title, due_datetime=None):
    """Adds a task with a title and optional due date."""
    
    try:
        service = get_google_service()
        
        # 1. Determine the task list ID (use the default primary list)
        task_lists = service.tasklists().list(maxResults=1).execute()
        # Fallback to the default list ID if the list is empty
        task_list_id = task_lists.get("items", [{}])[0].get("id", "@default")

        # 2. Calculate the due date
        # Note: Google Tasks API only supports dates, not times. If a time is specified,
        # we'll include it in the task title/notes and set the due date to midnight.
        time_specified = False
        time_str = None
        
        if due_datetime is None:
            # Default: tomorrow (next day) at midnight
            due_date = datetime.datetime.utcnow() + datetime.timedelta(days=1)
            due_date = due_date.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            # Check if a time was specified (not midnight)
            if due_datetime.hour != 0 or due_datetime.minute != 0:
                time_specified = True
                # Format time as HH:MM in 24-hour format
                time_str = f"{due_datetime.hour:02d}:{due_datetime.minute:02d}"
                # Set due date to just the date part (midnight)
                due_date = due_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                due_date = due_datetime
        
        # The Tasks API requires the due date to be a full RFC 3339 timestamp (ISO format)
        # Convert local time to UTC properly, but only the date part matters
        if due_date.tzinfo is None:
            # No timezone info, assume it's local time
            # Get the local timezone
            import time
            # Get timezone offset in seconds (negative for timezones ahead of UTC)
            if time.daylight:
                offset_seconds = -time.altzone
            else:
                offset_seconds = -time.timezone
            offset_hours = offset_seconds / 3600
            # Create timezone-aware datetime and convert to UTC
            local_tz = datetime.timezone(datetime.timedelta(hours=offset_hours))
            due_date_aware = due_date.replace(tzinfo=local_tz)
            due_date_utc = due_date_aware.astimezone(datetime.timezone.utc)
            due_date_iso = due_date_utc.isoformat("T").replace("+00:00", "Z")
        else:
            # Has timezone, convert to UTC and format
            due_date_utc = due_date.astimezone(datetime.timezone.utc)
            due_date_iso = due_date_utc.isoformat("T").replace("+00:00", "Z")
        
        # 3. Prepare task title and notes
        # If time was specified, add it to the title
        final_title = task_title
        notes = "Added automatically via Python script from Mac."
        
        if time_specified:
            # Add time to the title
            final_title = f"{task_title} at {time_str}"
            notes = f"Time: {time_str}\n{notes}"
        
        # 4. Define the task body
        task_body = {
            "title": final_title,
            "due": due_date_iso,
            "notes": notes,
        }

        # 5. Insert the task
        result = service.tasks().insert(tasklist=task_list_id, body=task_body).execute()
        print(f"✅ Success! Task created: {result['title']}")
        
        # Format the due date for display
        if 'due' in result:
            due_from_api = result['due']
            due_display = due_from_api.split('T')[0]
            if time_specified:
                print(f"   Due date: {due_display} at {time_str} (time included in title)")
            else:
                print(f"   Due date: {due_display}")
        else:
            print(f"   Due date: Not set")

    except HttpError as err:
        print(f"❌ An HTTP error occurred: {err}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ An error occurred: {e}")
        sys.exit(1)

def list_tasks(show_completed=False, max_results=100, json_output=False):
    """Lists all tasks from the default task list."""
    
    try:
        service = get_google_service()
        
        # 1. Determine the task list ID (use the default primary list)
        task_lists = service.tasklists().list(maxResults=1).execute()
        task_list_id = task_lists.get("items", [{}])[0].get("id", "@default")
        
        # 2. List tasks
        tasks_result = service.tasks().list(
            tasklist=task_list_id,
            showCompleted=show_completed,
            maxResults=max_results
        ).execute()
        
        tasks = tasks_result.get("items", [])
        
        if json_output:
            # Output as JSON with full task IDs
            import json
            # Format tasks for JSON output
            formatted_tasks = []
            for task in tasks:
                formatted_task = {
                    "id": task.get("id", ""),
                    "title": task.get("title", ""),
                    "status": task.get("status", ""),
                    "due": task.get("due", ""),
                    "notes": task.get("notes", ""),
                    "updated": task.get("updated", ""),
                    "position": task.get("position", "")
                }
                formatted_tasks.append(formatted_task)
            
            output = {
                "task_list_id": task_list_id,
                "count": len(formatted_tasks),
                "tasks": formatted_tasks
            }
            print(json.dumps(output, indent=2))
            return
        
        # Human-readable output
        if not tasks:
            print("No tasks found.")
            return
        
        print(f"\n📋 Found {len(tasks)} task(s):\n")
        
        for i, task in enumerate(tasks, 1):
            title = task.get("title", "(No title)")
            status = task.get("status", "")
            due = task.get("due", "")
            task_id = task.get("id", "")
            
            # Format status
            status_icon = "✅" if status == "completed" else "⏳"
            
            # Format due date
            due_str = ""
            if due:
                try:
                    # Parse the due date (format: 2025-12-30T00:00:00.000Z)
                    due_date = due.split("T")[0]
                    due_str = f" | Due: {due_date}"
                except:
                    due_str = f" | Due: {due}"
            
            # Show task ID for reference (truncated)
            id_display = f" [{task_id[:8]}...]" if task_id and len(task_id) > 8 else ""
            print(f"{i}. {status_icon} {title}{due_str}{id_display}")
        
        print(f"\n💡 To complete a task, use:")
        print(f"   python createTask.py complete <number>")
        print(f"   python createTask.py complete 'task title'")
        print(f"   python createTask.py complete <task-id>")
        print()
        
    except HttpError as err:
        print(f"❌ An HTTP error occurred: {err}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ An error occurred: {e}")
        sys.exit(1)

def complete_task(task_identifier):
    """
    Marks a task as completed.
    Can match by:
    - Task ID (if it looks like an ID)
    - Task title (partial match, case-insensitive)
    - Task number from list (if numeric)
    """
    
    try:
        service = get_google_service()
        
        # 1. Determine the task list ID (use the default primary list)
        task_lists = service.tasklists().list(maxResults=1).execute()
        task_list_id = task_lists.get("items", [{}])[0].get("id", "@default")
        
        # 2. Check if identifier looks like a task ID (long alphanumeric string)
        # Google Tasks IDs are typically long strings like "MDIzODQ2NzI2ODE4NDE4ODUyNzA6MTIzNDU2Nzg5MA"
        if len(task_identifier) > 20 and task_identifier.replace('_', '').replace('-', '').isalnum():
            # Looks like a task ID, fetch the task first, then update it
            try:
                # Get the existing task to preserve all fields
                existing_task = service.tasks().get(
                    tasklist=task_list_id,
                    task=task_identifier
                ).execute()
                
                # Update with all existing fields plus status change
                updated_task = existing_task.copy()
                updated_task["status"] = "completed"
                
                result = service.tasks().update(
                    tasklist=task_list_id,
                    task=task_identifier,
                    body=updated_task
                ).execute()
                print(f"🎯 Task completed: {result.get('title', '(No title)')}")
                return
            except HttpError as err:
                print(f"❌ Task ID not found or error: {err}")
                sys.exit(1)
        
        # 3. Check if identifier is a number (for selecting from list)
        if task_identifier.isdigit():
            # Get all incomplete tasks
            tasks_result = service.tasks().list(
                tasklist=task_list_id,
                showCompleted=False,
                maxResults=100
            ).execute()
            tasks = tasks_result.get("items", [])
            
            task_num = int(task_identifier)
            if task_num < 1 or task_num > len(tasks):
                print(f"❌ Task number {task_num} is out of range (1-{len(tasks)})")
                return
            
            task_to_complete = tasks[task_num - 1]
            task_id = task_to_complete.get("id")
            task_title_actual = task_to_complete.get("title", "(No title)")
        else:
            # 4. Search by title (case-insensitive, partial match)
            tasks_result = service.tasks().list(
                tasklist=task_list_id,
                showCompleted=False,  # Only search incomplete tasks
                maxResults=100
            ).execute()
            
            tasks = tasks_result.get("items", [])
            
            # Find matching tasks
            matching_tasks = []
            task_title_lower = task_identifier.lower()
            
            for task in tasks:
                task_title_from_api = task.get("title", "").lower()
                if task_title_lower in task_title_from_api or task_title_from_api in task_title_lower:
                    matching_tasks.append(task)
            
            if not matching_tasks:
                print(f"❌ No incomplete task found matching '{task_identifier}'")
                print(f"\n💡 Tip: Use 'python createTask.py list' to see all tasks with numbers")
                print(f"   Then use: python createTask.py complete <number>")
                return
            
            # 5. If multiple matches, show them and require selection
            if len(matching_tasks) > 1:
                print(f"⚠️  Found {len(matching_tasks)} matching tasks:\n")
                for i, task in enumerate(matching_tasks, 1):
                    title = task.get("title", "(No title)")
                    due = task.get("due", "")
                    due_str = ""
                    if due:
                        try:
                            due_date = due.split("T")[0]
                            due_str = f" | Due: {due_date}"
                        except:
                            pass
                    print(f"   {i}. {title}{due_str}")
                print(f"\n❌ Multiple matches found. Please be more specific:")
                print(f"   - Use the task number: python createTask.py complete <number>")
                print(f"   - Use more of the title: python createTask.py complete 'full task title'")
                print(f"   - Use the task ID: python createTask.py complete <task-id>")
                return
            
            # Single match found
            task_to_complete = matching_tasks[0]
            task_id = task_to_complete.get("id")
            task_title_actual = task_to_complete.get("title", "(No title)")
        
        # 6. Get the existing task first to preserve all fields
        existing_task = service.tasks().get(
            tasklist=task_list_id,
            task=task_id
        ).execute()
        
        # 7. Update the task with all existing fields plus status change
        updated_task = existing_task.copy()
        updated_task["status"] = "completed"
        
        result = service.tasks().update(
            tasklist=task_list_id,
            task=task_id,
            body=updated_task
        ).execute()
        
        print(f"✅ Task completed: {result.get('title', task_title_actual)}")
        
    except HttpError as err:
        print(f"❌ An HTTP error occurred: {err}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ An error occurred: {e}")
        sys.exit(1)

def delete_task(task_identifier):
    """
    Deletes a task by ID or by matching title/number.
    Can match by:
    - Task ID (if it looks like an ID)
    - Task title (partial match, case-insensitive)
    - Task number from list (if numeric)
    """
    
    try:
        service = get_google_service()
        
        # 1. Determine the task list ID (use the default primary list)
        task_lists = service.tasklists().list(maxResults=1).execute()
        task_list_id = task_lists.get("items", [{}])[0].get("id", "@default")
        
        # 2. Check if identifier looks like a task ID (long alphanumeric string)
        if len(task_identifier) > 20 and task_identifier.replace('_', '').replace('-', '').isalnum():
            # Looks like a task ID, try to delete it directly
            try:
                # First, try to get the task to show what we're deleting
                task = service.tasks().get(tasklist=task_list_id, task=task_identifier).execute()
                task_title = task.get("title", "(No title)")
                
                # Delete the task
                service.tasks().delete(tasklist=task_list_id, task=task_identifier).execute()
                print(f"🗑️️ Task deleted: {task_title}")
                return
            except HttpError as err:
                if err.resp.status == 404:
                    print(f"❌ Task ID not found: {task_identifier}")
                else:
                    print(f"❌ Error deleting task: {err}")
                sys.exit(1)
        
        # 3. Check if identifier is a number (for selecting from list)
        if task_identifier.isdigit():
            # Get all tasks (including completed for deletion)
            tasks_result = service.tasks().list(
                tasklist=task_list_id,
                showCompleted=True,
                maxResults=100
            ).execute()
            tasks = tasks_result.get("items", [])
            
            task_num = int(task_identifier)
            if task_num < 1 or task_num > len(tasks):
                print(f"❌ Task number {task_num} is out of range (1-{len(tasks)})")
                return
            
            task_to_delete = tasks[task_num - 1]
            task_id = task_to_delete.get("id")
            task_title_actual = task_to_delete.get("title", "(No title)")
        else:
            # 4. Search by title (case-insensitive, partial match)
            tasks_result = service.tasks().list(
                tasklist=task_list_id,
                showCompleted=True,  # Include completed tasks for deletion
                maxResults=100
            ).execute()
            
            tasks = tasks_result.get("items", [])
            
            # Find matching tasks
            matching_tasks = []
            task_title_lower = task_identifier.lower()
            
            for task in tasks:
                task_title_from_api = task.get("title", "").lower()
                if task_title_lower in task_title_from_api or task_title_from_api in task_title_lower:
                    matching_tasks.append(task)
            
            if not matching_tasks:
                print(f"❌ No task found matching '{task_identifier}'")
                return
            
            # 5. If multiple matches, show them and require selection
            if len(matching_tasks) > 1:
                print(f"⚠️  Found {len(matching_tasks)} matching tasks:\n")
                for i, task in enumerate(matching_tasks, 1):
                    title = task.get("title", "(No title)")
                    status = task.get("status", "")
                    due = task.get("due", "")
                    status_icon = "✅" if status == "completed" else "⏳"
                    due_str = ""
                    if due:
                        try:
                            due_date = due.split("T")[0]
                            due_str = f" | Due: {due_date}"
                        except:
                            pass
                    print(f"   {i}. {status_icon} {title}{due_str}")
                print(f"\n❌ Multiple matches found. Please be more specific:")
                print(f"   - Use the task number: python createTask.py delete <number>")
                print(f"   - Use more of the title: python createTask.py delete 'full task title'")
                print(f"   - Use the task ID: python createTask.py delete <task-id>")
                return
            
            # Single match found
            task_to_delete = matching_tasks[0]
            task_id = task_to_delete.get("id")
            task_title_actual = task_to_delete.get("title", "(No title)")
        
        # 6. Delete the task
        service.tasks().delete(tasklist=task_list_id, task=task_id).execute()
        print(f"❌️ Task deleted: {task_title_actual}")
        
    except HttpError as err:
        print(f"❌ An HTTP error occurred: {err}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ An error occurred: {e}")
        sys.exit(1)

def parse_task_input_to_alfred(user_input):
    """
    Parses user input for task creation and returns Alfred JSON format.
    
    Args:
        user_input: String like "Buy groceries 12-30:15:30"
    
    Returns:
        Alfred JSON with:
        - title: Cleaned task title (without date)
        - subtitle: Due date if specified
        - arg: JSON string with task data (title, due_datetime, etc.)
    """
    import json
    
    # Parse the input string
    task_title, due_datetime = parse_date_from_string(user_input)
    
    # Build subtitle with due date info
    subtitle = "No due date"
    if due_datetime:
        # Format the due date nicely
        if due_datetime.hour != 0 or due_datetime.minute != 0:
            # Has time - include it in subtitle
            time_str = f"{due_datetime.hour:02d}:{due_datetime.minute:02d}"
            due_date_str = due_datetime.strftime("%Y-%m-%d")
            subtitle = f"📆 Due: {due_date_str} at {time_str}"
        else:
            # Just date
            due_date_str = due_datetime.strftime("%Y-%m-%d")
            # Check if it's a relative date
            now = datetime.datetime.now()
            if due_datetime.date() == now.date():
                subtitle = "📅 Due: today"
            elif due_datetime.date() == (now + datetime.timedelta(days=1)).date():
                subtitle = "📅 Due: tomorrow"
            else:
                subtitle = f"📅 Due: {due_date_str}"
    
    # Build argument JSON with all task creation data
    task_data = {
        "title": task_title.strip() if task_title else "",
        "due_datetime": due_datetime.isoformat() if due_datetime else None,
        "has_time": due_datetime and (due_datetime.hour != 0 or due_datetime.minute != 0) if due_datetime else False
    }
    
    # Create Alfred item
    alfred_item = {
        "uid": "new_task",
        "title": task_title.strip() if task_title else "(No title)",
        "subtitle": subtitle,
        "arg": json.dumps(task_data),
        "valid": True if task_title and task_title.strip() else False,
        "icon": {
            "path": "icon.png"
        }
    }
    
    # Output Alfred JSON format
    alfred_output = {
        "items": [alfred_item]
    }
    
    print(json.dumps(alfred_output, indent=2))

def output_alfred_items(tasks_json):
    """
    Converts task list JSON to Alfred script filter format.
    Takes JSON string or dict from list_tasks() and outputs Alfred items.
    """
    import json
    
    # Parse JSON if it's a string
    if isinstance(tasks_json, str):
        data = json.loads(tasks_json)
    else:
        data = tasks_json
    
    # Extract tasks
    tasks = data.get("tasks", [])
    
    # Build Alfred items
    alfred_items = []
    
    for task in tasks:
        task_id = task.get("id", "")
        title = task.get("title", "(No title)")
        status = task.get("status", "")
        due = task.get("due", "")
        notes = task.get("notes", "")
        
        # Format subtitle with status and due date
        subtitle_parts = []
        
        # Status
        if status == "completed":
            subtitle_parts.append("✅ Completed")
        else:
            subtitle_parts.append("⏳ Pending")
        
        # Due date
        if due:
            try:
                # Parse the due date (format: 2025-12-30T00:00:00.000Z)
                due_date = due.split("T")[0]
                subtitle_parts.append(f"Due: {due_date}")
            except:
                subtitle_parts.append(f"Due: {due}")
        
        # Notes preview (first line, truncated)
        if notes:
            notes_preview = notes.split("\n")[0]
            if len(notes_preview) > 50:
                notes_preview = notes_preview[:47] + "..."
            subtitle_parts.append(notes_preview)
        
        subtitle = " | ".join(subtitle_parts) if subtitle_parts else "No details"
        
        # Create Alfred item
        item = {
            "uid": task_id,  # Unique identifier
            "title": title,
            "subtitle": subtitle,
            "arg": task_id,  # Task ID passed as argument
            "valid": True,
            "icon": {
                "path": "icon.png" if status != "completed" else "icon_completed.png"
            }
        }
        
        alfred_items.append(item)
    
    # Output Alfred JSON format
    alfred_output = {
        "items": alfred_items
    }
    
    print(json.dumps(alfred_output, indent=2))

if __name__ == "__main__":
    # Check for parse command (for Alfred input preview)
    if len(sys.argv) > 1 and sys.argv[1].lower() in ['parse', '--parse', '-p']:
        # Parse user input and output Alfred format
        if len(sys.argv) < 3:
            # No input provided, show empty result
            import json
            alfred_output = {
                "items": [{
                    "uid": "empty",
                    "title": "Type a task...",
                    "subtitle": "e.g., 'Buy groceries 12-30:15:30'",
                    "valid": False
                }]
            }
            print(json.dumps(alfred_output, indent=2))
        else:
            input_string = " ".join(sys.argv[2:])
            parse_task_input_to_alfred(input_string)
    # Check for list command
    elif len(sys.argv) > 1 and sys.argv[1].lower() in ['list', '--list', '-l']:
        # List tasks
        show_completed = '--completed' in sys.argv or '-c' in sys.argv
        json_output = '--json' in sys.argv or '-j' in sys.argv
        alfred_output = '--alfred' in sys.argv or '-a' in sys.argv
        
        if alfred_output:
            # Get tasks as JSON and convert to Alfred format
            import json
            import io
            import contextlib
            
            # Capture the JSON output
            f = io.StringIO()
            with contextlib.redirect_stdout(f):
                list_tasks(show_completed=show_completed, json_output=True)
            tasks_json = f.getvalue()
            
            # Convert to Alfred format
            output_alfred_items(tasks_json)
        else:
            list_tasks(show_completed=show_completed, json_output=json_output)
    # Check for complete command
    elif len(sys.argv) > 1 and sys.argv[1].lower() in ['complete', 'done', '--complete', '--done']:
        # Complete a task
        if len(sys.argv) < 3:
            print("❌ ERROR: Please provide a task identifier to complete.")
            print("Usage: python createTask.py complete 'Task title' or <task-id> or <number>")
            sys.exit(1)
        task_identifier = " ".join(sys.argv[2:])
        complete_task(task_identifier)
    # Check for delete command
    elif len(sys.argv) > 1 and sys.argv[1].lower() in ['delete', 'remove', '--delete', '--remove', '-d']:
        # Delete a task
        if len(sys.argv) < 3:
            print("❌ ERROR: Please provide a task identifier to delete.")
            print("Usage: python createTask.py delete 'Task title' or <task-id> or <number>")
            sys.exit(1)
        task_identifier = " ".join(sys.argv[2:])
        delete_task(task_identifier)
    # Check for create from JSON command
    elif len(sys.argv) > 1 and sys.argv[1].lower() in ['create', '--create', '-c']:
        # Create task from JSON (from Alfred parse output)
        if len(sys.argv) < 3:
            print("❌ ERROR: Please provide JSON task data.")
            print("Usage: python createTask.py create '{\"title\": \"Task\", \"due_datetime\": \"...\"}'")
            sys.exit(1)
        import json
        try:
            # Join all arguments after 'create' in case JSON has spaces
            json_str = " ".join(sys.argv[2:])
            task_data = json.loads(json_str)
            
            # Extract task data
            task_title = task_data.get("title", "")
            due_datetime_str = task_data.get("due_datetime")
            
            # Parse due_datetime if provided
            due_datetime = None
            if due_datetime_str:
                try:
                    due_datetime = datetime.datetime.fromisoformat(due_datetime_str.replace('Z', '+00:00'))
                    # Convert to local time if it has timezone
                    if due_datetime.tzinfo:
                        due_datetime = due_datetime.astimezone().replace(tzinfo=None)
                except:
                    # Try parsing as ISO format
                    try:
                        due_datetime = datetime.datetime.fromisoformat(due_datetime_str)
                    except:
                        print(f"⚠️  Warning: Could not parse due_datetime: {due_datetime_str}")
            
            if not task_title:
                print("❌ ERROR: Task title is required in JSON data.")
                sys.exit(1)
            
            # Create the task
            add_task(task_title, due_datetime)
        except json.JSONDecodeError as e:
            print(f"❌ ERROR: Invalid JSON: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ ERROR: {e}")
            sys.exit(1)
    elif len(sys.argv) > 1:
        # Create a task
        input_string = " ".join(sys.argv[1:])  # Join all arguments in case title has spaces
        
        # Parse date from input string
        task_title, due_datetime = parse_date_from_string(input_string)
        
        if not task_title or not task_title.strip():
            print("❌ ERROR: Task title cannot be empty after removing date.")
            print("Usage: python createTask.py 'Your task title here [date]'")
            sys.exit(1)
        
        # Add the task
        add_task(task_title.strip(), due_datetime)
    else:
        print("❌ ERROR: Please provide a task title as an argument.")
        print("\nUsage:")
        print("  Create task:    python createTask.py 'Your task title here [date]'")
        print("  Create from JSON: python createTask.py create '{\"title\": \"Task\", ...}'")
        print("  Parse input:    python createTask.py parse 'Task title [date]'")
        print("  List tasks:     python createTask.py list")
        print("  List all:       python createTask.py list --completed")
        print("  List JSON:      python createTask.py list --json")
        print("  List Alfred:    python createTask.py list --alfred")
        print("  Complete:       python createTask.py complete 'Task title' or <task-id> or <number>")
        print("  Delete:         python createTask.py delete 'Task title' or <task-id> or <number>")
        print("\nDate formats supported:")
        print("  - mm-dd (e.g., 11-23)")
        print("  - yyyy-mm-dd (e.g., 2025-12-03)")
        print("  - mm-dd:hh:mm (e.g., 11-23:23:30)")
        print("  - today, tomorrow")
        print("  - next monday, next tuesday, etc.")
        print("  - monday, tuesday, etc. (next occurrence)")
        sys.exit(1)
