import re, os, asyncio, json, random
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from quart import Quart
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

def process_questions(content):
    questions = re.split(r'\n(?=\d+\.)', content.strip())
    quiz_data = []
    
    for question in questions:
        try:
            lines = question.strip().split('\n')
            q_text = lines[0].split('.', 1)[1].strip()
            options = [line.strip() for line in lines[1:] if re.match(r'^[A-I]\)', line.strip())]
            
            answer_line = next((line for line in lines if line.startswith("Answer:")), None)
            if answer_line:
                answer_parts = answer_line.split(',', 1)
                answer = answer_parts[0].split(':')[1].strip()
                explanation = answer_parts[1].strip() if len(answer_parts) > 1 else None
                correct_option_id = next((i for i, opt in enumerate(options) if opt.startswith(f"{answer})")), None)
            else:
                correct_option_id = None
                explanation = None
            
            quiz_data.append({
                "question": q_text,
                "options": [opt.split(')', 1)[1].strip() for opt in options],
                "correct_option_id": correct_option_id,
                "explanation": explanation
            })
        except Exception as e:
            print(f"Error processing question: {question}\nError: {e}")
    
    return quiz_data

async def send_polls(client: Client, chat_id: int, questions: list, start: int = 0, end: int = None):
    end = end or len(questions)
    for question in questions[start:end]:
        try:
            is_quiz = question["correct_option_id"] is not None
            await client.send_poll(
                chat_id,
                question["question"],
                options=question["options"],
                type=enums.PollType.QUIZ if is_quiz else enums.PollType.REGULAR,
                correct_option_id=question["correct_option_id"] if is_quiz else None,
                is_anonymous=False,
                explanation=question.get("explanation") if is_quiz else None
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
"""
    await message.reply_text(help_text)

@app.on_message(filters.private & (filters.document | filters.text) & ~filters.bot)
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

web = Quart(__name__)

@web.route("/")
async def index():
    return "Quiz Bot is running!"

async def main():
    await web.run_task(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    asyncio.get_event_loop().create_task(main())
    app.run()