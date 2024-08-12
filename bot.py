import re, os, asyncio, json
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.types import Message
from quart import Quart

# Telegram API credentials
API_ID = int(os.environ.get('API_ID'))
API_HASH = os.environ.get('API_HASH')
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# Initialize the Pyrogram client
app = Client(
  "quiz_bot",
  api_id=API_ID,
  api_hash=API_HASH,
  bot_token=BOT_TOKEN)

# File to store loaded quiz files
LOADED_FILES = "loaded_files.json"

def load_files():
    if os.path.exists(LOADED_FILES):
        with open(LOADED_FILES, 'r') as f:
            return json.load(f)
    return {}

def save_files(files):
    with open(LOADED_FILES, 'w') as f:
        json.dump(files, f)

def process_questions(content):
    questions = re.split(r'\n\s*\n(?=\d+\.)', content.strip())
    quiz_data = []
    
    for question in questions:
        try:
            lines = question.strip().split('\n')
            
            q_text = lines[0].split('.', 1)[1].strip()
            options = [line.split(') ', 1)[1].strip() for line in lines[1:5]]
            
            answer_line = lines[-1].strip()
            if answer_line.startswith("Answer:"):
                answer_parts = answer_line.split(',', 1)
                answer = answer_parts[0].split(':')[1].strip()
                explanation = answer_parts[1].strip() if len(answer_parts) > 1 else None
                correct_option_index = {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(answer)
            else:
                correct_option_index = None
                explanation = None
            
            quiz_data.append({
                "question": q_text,
                "options": options,
                "correct_option_id": correct_option_index,
                "explanation": explanation
            })
        
        except Exception as e:
            print(f"Error processing question: {question}\nError: {e}")
    
    return quiz_data

def read_questions(file_path):
    try:
        with open(file_path, 'r') as file:
            content = file.read()
        return process_questions(content)
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return []
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return []

async def send_polls(client: Client, chat_id: int, questions: list, start: int = 0, end: int = None):
    end = end or len(questions)
    for question in questions[start:end]:
        try:
            if question["correct_option_id"] is not None:
                await client.send_poll(
                    chat_id,
                    question["question"],
                    options=question["options"],
                    type=enums.PollType.QUIZ,
                    correct_option_id=question["correct_option_id"],
                    is_anonymous=False,
                    explanation=question["explanation"] if question["explanation"] else None
                )
            else:
                await client.send_poll(
                    chat_id,
                    question["question"],
                    options=question["options"],
                    type=enums.PollType.REGULAR,
                    is_anonymous=False
                )
            await asyncio.sleep(3)
        except FloodWait as e:
            print(f"FloodWait: Sleeping for {e.value} seconds")
            await asyncio.sleep(e.value)
        except Exception as e:
            print(f"Error sending poll: {e}")

@app.on_message(filters.command("poll"))
async def quiz(client, message):
    chat_id = message.chat.id
    
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        file_name = args[1].strip()
    else:
        file_name = 'questions.txt'
    
    questions = read_questions(file_name)
    
    if not questions:
        await message.reply_text(f"No valid questions found in the file: {file_name}")
        return
    
    await send_polls(client, chat_id, questions)
    await message.reply_text("Quiz completed!")

async def is_admin(client, chat_id, user_id):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception:
        return False

@app.on_message(filters.command("gen"))
async def generate_quiz_from_file(client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not await is_admin(client, chat_id, user_id):
        await message.reply_text("Only group admins can use this command.")
        return

    args = message.text.split()[1:]
    file_name = args[0] if args else None
    start = 0
    end = None

    # Parse start and end if provided
    if len(args) > 1:
        try:
            start = int(args[-2]) - 1
            end = int(args[-1])
        except ValueError:
            pass  # If conversion fails, assume no start/end was provided

    loaded_files = load_files()
    file_path = None

    # Case 1: File specified in command
    if file_name:
        if str(chat_id) in loaded_files:
            for loaded_file, path in loaded_files[str(chat_id)].items():
                if loaded_file.endswith(file_name):
                    file_path = path
                    break
        if not file_path:
            await message.reply_text(f"File '{file_name}' is not loaded. Please load it first using the /load command.")
            return

    # Case 2: Reply to a document
    elif message.reply_to_message and message.reply_to_message.document:
        document = message.reply_to_message.document
        if document.file_name.split('.')[-1].lower() != 'txt':
            await message.reply_text("Please upload a text (.txt) file.")
            return
        
        # Check if the replied document is already loaded
        if str(chat_id) in loaded_files:
            for loaded_file, path in loaded_files[str(chat_id)].items():
                if loaded_file.endswith(document.file_name):
                    file_path = path
                    break
        
        # If not loaded, download it temporarily
        if not file_path:
            file_path = await client.download_media(document, file_name=f"temp_{chat_id}_{document.file_name}")

    else:
        await message.reply_text("Please either specify a loaded file name or reply to a document with the /gen command.")
        return

    questions = read_questions(file_path)

    # Remove temporary file if it was just downloaded
    if file_path.startswith("temp_"):
        os.remove(file_path)

    if not questions:
        await message.reply_text("No valid questions found in the file.")
        return

    await message.reply_text("Starting the quiz...")
    await send_polls(client, chat_id, questions, start, end)
    await message.reply_text("Quiz completed!")

@app.on_message(filters.command("load"))
async def load_file(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not await is_admin(client, chat_id, user_id):
        await message.reply_text("Only group admins can load files.")
        return

    if message.reply_to_message and message.reply_to_message.document:
        document = message.reply_to_message.document
        if document.file_name.split('.')[-1].lower() != 'txt':
            await message.reply_text("Please upload a text (.txt) file.")
            return

        if document.file_size > 5 * 1024 * 1024:  # 5MB in bytes
            await message.reply_text("File size exceeds 5MB limit.")
            return

        file_name = document.file_name
        loaded_files = load_files()

        if str(chat_id) not in loaded_files:
            loaded_files[str(chat_id)] = {}

        # Check if file name already exists and add number if needed
        base_name, ext = os.path.splitext(file_name)
        counter = 1
        while file_name in loaded_files[str(chat_id)]:
            file_name = f"{base_name}_{counter}{ext}"
            counter += 1

        file_path = await client.download_media(document, file_name=f"{chat_id}_{file_name}")
        loaded_files[str(chat_id)][file_name] = file_path
        save_files(loaded_files)

        await message.reply_text(f"File '{file_name}' has been loaded.")
    else:
        await message.reply_text("Please reply to a document message with the /load command.")

@app.on_message(filters.command("list"))
async def list_files(client, message):
    chat_id = str(message.chat.id)
    loaded_files = load_files()
    
    if chat_id in loaded_files and loaded_files[chat_id]:
        file_list = "\n".join(loaded_files[chat_id].keys())
        await message.reply_text(f"Loaded files for this group:\n{file_list}")
    else:
        await message.reply_text("No files are currently loaded for this group.")

@app.on_message(filters.command("del"))
async def delete_file(client, message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    
    if not await is_admin(client, chat_id, user_id):
        await message.reply_text("Only group admins can delete files.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Please provide a file name to delete.")
        return

    file_name = args[1].strip()
    loaded_files = load_files()

    if chat_id not in loaded_files:
        await message.reply_text("No files are loaded for this group.")
        return

    if file_name == "all":
        for file_path in loaded_files[chat_id].values():
            if os.path.exists(file_path):
                os.remove(file_path)
        loaded_files[chat_id].clear()
        save_files(loaded_files)
        await message.reply_text("All loaded files for this group have been deleted.")
    elif file_name in loaded_files[chat_id]:
        file_path = loaded_files[chat_id][file_name]
        if os.path.exists(file_path):
            os.remove(file_path)
        del loaded_files[chat_id][file_name]
        save_files(loaded_files)
        await message.reply_text(f"File '{file_name}' has been deleted.")
    else:
        await message.reply_text(f"File '{file_name}' is not loaded for this group.")

web = Quart(__name__)

@web.route("/")
async def index():
    return "Quiz Bot is running!"

if __name__ == "__main__":
    app.start()
    web.run_task(host="0.0.0.0", port=8080)
