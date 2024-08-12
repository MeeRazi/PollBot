import re, os, asyncio, json, random
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from quart import Quart
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import docx2txt
from bs4 import BeautifulSoup

# Telegram API credentials
API_ID = int(os.environ.get('API_ID'))
API_HASH = os.environ.get('API_HASH')
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# Initialize the Pyrogram client
app = Client("quiz_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# File to store loaded quiz files
LOADED_FILES = "loaded_files.json"
QUIZ_DATA = "quiz_data.json"
SETTINGS = "settings.json"
GLOBAL_POLLS = "global_polls.json"

OWNER_ID = 2154687955

def load_json_file(filename):
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            return json.load(f)
    return {}

def save_json_file(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f)

def generate_quiz_id():
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))

def save_quiz_data(quiz_id, questions):
    quiz_data = load_json_file(QUIZ_DATA)
    quiz_data[quiz_id] = questions
    save_json_file(QUIZ_DATA, quiz_data)

def load_quiz_data(quiz_id):
    quiz_data = load_json_file(QUIZ_DATA)
    return quiz_data.get(quiz_id)

def extract_text_from_file(file_path):
    _, file_extension = os.path.splitext(file_path)
    file_extension = file_extension.lower()

    if file_extension == '.txt':
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    elif file_extension == '.docx':
        return docx2txt.process(file_path)
    elif file_extension == '.html':
        with open(file_path, 'r', encoding='utf-8') as file:
            soup = BeautifulSoup(file, 'html.parser')
            return soup.get_text()
    else:
        raise ValueError(f"Unsupported file type: {file_extension}")
    
def read_example_file():
    example_file_path = "example.txt"
    if os.path.exists(example_file_path):
        with open(example_file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        return process_questions(content)
    else:
        print(f"Warning: {example_file_path} not found.")
        return []    

def process_questions(content):
    questions = re.split(r'\n\s*\n(?=\d+\.)', content.strip())
    quiz_data = []
    
    for question in questions:
        try:
            lines = question.strip().split('\n')
            q_text = lines[0].split('.', 1)[1].strip()
            options = [line.split(') ', 1)[1].strip() for line in lines[1:-1] if ') ' in line]
            
            answer_line = lines[-1].strip()
            if answer_line.startswith("Answer:"):
                answer_parts = answer_line.split(',', 1)
                answer = answer_parts[0].split(':')[1].strip()
                explanation = answer_parts[1].strip() if len(answer_parts) > 1 else None
                correct_option_index = next((i for i, opt in enumerate(options) if opt.startswith(answer)), None)
            else:
                correct_option_index = None
                explanation = None
            
            if q_text and options:
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
        content = extract_text_from_file(file_path)
        questions = process_questions(content)
        if not questions:
            print(f"No valid questions found in file: {file_path}")
            print(f"File content: {content[:500]}...")  # Print first 500 characters for debugging
        return questions
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

async def is_admin(client, chat_id, user_id):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except Exception:
        return False

@app.on_message(filters.command("poll"))
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

    if len(args) > 1:
        try:
            start = int(args[-2]) - 1
            end = int(args[-1])
        except ValueError:
            pass

    loaded_files = load_json_file(LOADED_FILES)
    file_path = None

    if file_name:
        if str(chat_id) in loaded_files:
            for loaded_file, path in loaded_files[str(chat_id)].items():
                if loaded_file.endswith(file_name):
                    file_path = path
                    break
        if not file_path:
            await message.reply_text(f"File '{file_name}' is not loaded. Please load it first using the /load command.")
            return

    elif message.reply_to_message and message.reply_to_message.document:
        document = message.reply_to_message.document
        file_path = await client.download_media(document, file_name=f"temp_{chat_id}_{document.file_name}")

    else:
        await message.reply_text("Please either specify a loaded file name or reply to a document with the /poll command.")
        return

    questions = read_questions(file_path)

    if file_path.startswith("temp_"):
        os.remove(file_path)

    if not questions:
        await message.reply_text("No valid questions found in the file. Please check the file format and try again.")
        return

    m = await message.reply_text("Starting the quiz...")
    await send_polls(client, chat_id, questions, start, end)
    await m.edit("Quiz completed!")

@app.on_message(filters.command("load"))
async def load_file(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not await is_admin(client, chat_id, user_id):
        await message.reply_text("Only group admins can load files.")
        return

    if message.reply_to_message and message.reply_to_message.document:
        document = message.reply_to_message.document
        if document.file_size > 1024 * 1024:  # 1MB in bytes
            await message.reply_text("File size exceeds 1MB limit.")
            return

        file_name = document.file_name
        loaded_files = load_json_file(LOADED_FILES)

        if str(chat_id) not in loaded_files:
            loaded_files[str(chat_id)] = {}

        base_name, ext = os.path.splitext(file_name)
        counter = 1
        while file_name in loaded_files[str(chat_id)]:
            file_name = f"{base_name}_{counter}{ext}"
            counter += 1

        file_path = await client.download_media(document, file_name=f"{chat_id}_{file_name}")
        loaded_files[str(chat_id)][file_name] = file_path
        save_json_file(LOADED_FILES, loaded_files)

        await message.reply_text(f"File '{file_name}' has been loaded.")
    else:
        await message.reply_text("Please reply to a document message with the /load command.")

@app.on_message(filters.command("list"))
async def list_files(client, message):
    chat_id = str(message.chat.id)
    loaded_files = load_json_file(LOADED_FILES)
    
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
    loaded_files = load_json_file(LOADED_FILES)

    if chat_id not in loaded_files:
        await message.reply_text("No files are loaded for this group.")
        return

    if file_name == "all":
        for file_path in loaded_files[chat_id].values():
            if os.path.exists(file_path):
                os.remove(file_path)
        loaded_files[chat_id].clear()
        save_json_file(LOADED_FILES, loaded_files)
        await message.reply_text("All loaded files for this group have been deleted.")
    elif file_name in loaded_files[chat_id]:
        file_path = loaded_files[chat_id][file_name]
        if os.path.exists(file_path):
            os.remove(file_path)
        del loaded_files[chat_id][file_name]
        save_json_file(LOADED_FILES, loaded_files)
        await message.reply_text(f"File '{file_name}' has been deleted.")
    else:
        await message.reply_text(f"File '{file_name}' is not loaded for this group.")

@app.on_message(filters.command("start"))
async def start(client, message):
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("quiz-"):
        quiz_id = args[1][5:]
        questions = load_quiz_data(quiz_id)
        if questions:
            await send_polls(client, message.chat.id, questions)
        else:
            await message.reply_text("Invalid or expired quiz link.")
    else:
        button = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Add me to a group", url=f"t.me/{(await client.get_me()).username}?startgroup=quiz&admin=change_info")]]
        )
        await message.reply_text("I am a quiz bot. Please add me to a group to start a quiz!", reply_markup=button)

@app.on_message(filters.command("help"))
async def help_command(client, message):
    help_text = """
Here's how to use the Quiz Bot:

1. Load a quiz file:
   Reply to a file (txt, docx, html, pdf, odt) with the /load command.
   File format should be:
   ```
   1. Question text
   A) Option A
   B) Option B
   C) Option C
   D) Option D
   Answer: A, Explanation (optional)

   2. Next question...
   ```
   Note: You can have 1 to 9 options per question.

2. Generate a quiz:
   Use /poll <filename> [start] [end] to generate polls.
3. List loaded files:
   Use /list to see all loaded files.
4. Delete a file:
   Use /del <filename> to remove a loaded file.
5. Share a quiz:
   Send a file or a message in the correct format, and the bot will generate a shareable link.
6. Random Quiz:
   The bot posts a random quiz every hour. Use /random_quiz to toggle this feature (on by default).
    """
    await message.reply_text(help_text)

@app.on_message(filters.private & (filters.document | filters.text))
async def generate_quiz_link(client, message):
    questions = []
    if message.document:
        file_path = await client.download_media(message.document)
        content = extract_text_from_file(file_path)
        os.remove(file_path)
        questions = process_questions(content)
    elif message.text:
        questions = process_questions(message.text)
    
    if questions:
        quiz_id = generate_quiz_id()
        save_quiz_data(quiz_id, questions)
        bot_username = (await client.get_me()).username
        quiz_link = f"https://t.me/{bot_username}?startgroup=quiz-{quiz_id}"
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Start Quiz", url=quiz_link)]])
        await message.reply_text("Quiz created! Share this link to start the quiz:", reply_markup=button)
    else:
        await message.reply_text("No valid questions found. Please check the format and try again.")

async def send_random_quiz():
    settings = load_json_file(SETTINGS)
    quiz_groups = settings.get('quiz_groups', [])
    
    if not quiz_groups:
        return

    loaded_files = load_json_file(LOADED_FILES)
    all_questions = []
    for chat_files in loaded_files.values():
        for file_path in chat_files.values():
            all_questions.extend(read_questions(file_path))
    
    global_polls = load_json_file(GLOBAL_POLLS)
    all_questions.extend(global_polls)

    # Add questions from example.txt
    example_questions = read_example_file()
    all_questions.extend(example_questions)

    if not all_questions:
        print("No questions available for random quiz.")
        return

    for chat_id in quiz_groups:
        if settings.get('random_quiz_enabled', {}).get(chat_id, True):  # Default is True
            try:
                random_question = random.choice(all_questions)
                await send_polls(app, int(chat_id), [random_question])
            except Exception as e:
                print(f"Error sending random quiz to {chat_id}: {e}")

@app.on_message(filters.command("random_quiz") & filters.group)
async def toggle_random_quiz(client, message):
    user_id = message.from_user.id
    chat_id = str(message.chat.id)
    
    if not await is_admin(client, chat_id, user_id):
        await message.reply_text("Only group admins can use this command.")
        return

    settings = load_json_file(SETTINGS)
    current_state = settings.get('random_quiz_enabled', {}).get(chat_id, True)  # Default is True
    new_state = not current_state
    
    if 'random_quiz_enabled' not in settings:
        settings['random_quiz_enabled'] = {}
    settings['random_quiz_enabled'][chat_id] = new_state
    
    if 'quiz_groups' not in settings:
        settings['quiz_groups'] = []
    
    if new_state and chat_id not in settings['quiz_groups']:
        settings['quiz_groups'].append(chat_id)
    elif not new_state and chat_id in settings['quiz_groups']:
        settings['quiz_groups'].remove(chat_id)
    
    save_json_file(SETTINGS, settings)
    await message.reply_text(f"Random quiz feature has been {'enabled' if new_state else 'disabled'} for this group.")

@app.on_message(filters.command("update") & filters.private)
async def update_global_poll(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("This command is only available to the bot owner.")
        return

    if not message.reply_to_message or not message.reply_to_message.text:
        await message.reply_text("Please reply to a message containing the global poll question.")
        return

    content = message.reply_to_message.text
    questions = process_questions(content)

    if not questions:
        await message.reply_text("No valid questions found. Please check the format and try again.")
        return

    global_polls = load_json_file(GLOBAL_POLLS)
    global_polls.extend(questions)
    save_json_file(GLOBAL_POLLS, global_polls)

    await message.reply_text(f"{len(questions)} question(s) added to the global polls.")

@app.on_message(filters.command("delete") & filters.private)
async def delete_global_poll(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("This command is only available to the bot owner.")
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.reply_text("Please provide the index of the global poll to delete.")
        return

    index = int(args[1]) - 1
    global_polls = load_json_file(GLOBAL_POLLS)

    if index < 0 or index >= len(global_polls):
        await message.reply_text("Invalid index. Please provide a valid index.")
        return

    deleted_poll = global_polls.pop(index)
    save_json_file(GLOBAL_POLLS, global_polls)

    await message.reply_text(f"Global poll deleted:\n{deleted_poll['question']}")

# Initialize scheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(send_random_quiz, 'interval', hours=1)
scheduler.start()

web = Quart(__name__)

@web.route("/")
async def index():
    return "Quiz Bot is running!"

async def main():
    await web.run_task(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    asyncio.get_event_loop().create_task(main())
    app.run()