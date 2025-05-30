from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask import send_from_directory
from flask_socketio import SocketIO, emit
from datetime import datetime
import time
import os
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import base64
import mimetypes
import mimetypes
import uuid
import shutil
from threading import Timer

app=Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = 'thedaysofthejaguar'
socketio = SocketIO(app, cors_allowed_origins=["http://localhost:9000", "http://127.0.0.1:9000"], cors_credentials=True,manage_session=True, logger =True, engineio_logger=True)
users = {}
UPLOAD_FOLDER = 'uploads'
app.config['SESSION_COOKIE_SAMESITE']= 'Lax' #Allows cookies to be sent by webSocket
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def init_db():
	conn = sqlite3.connect('users.db')
	cur = conn.cursor()
	cur.execute('''
	CREATE TABLE IF NOT EXISTS
	users(
	id INTEGER PRIMARY KEY
	AUTOINCREMENT,
	username TEXT UNIQUE NOT NULL,
	email TEXT UNIQUE NOT NULL, 
	password TEXT NOT NULL
	)
	''')
	cur.execute('''
	CREATE TABLE IF NOT EXISTS
	messages(
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	username TEXT NOT NULL,
	message TEXT NOT NULL,
	timestamp REAL NOT NULL,
	message_type TEXT NOT NULL DEFAULT 'text'
	)
	''')
	conn.commit()
	conn.close()

init_db()

@app.route('/')
def index():
	return redirect(url_for('home'))
	
@app.route('/home')
def home():
	if 'user' in session:
		return redirect(url_for('chat'))
	else:
		return render_template('home.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
	response = send_from_directory(app.config['UPLOAD_FOLDER'], filename)
	#Add Mime type detection
	mime_type, _ = mimetypes.guess_type(filename)
	if mime_type:
		response.headers.set('Content-Type', mime_type)
	return response

@socketio.on('connect')
def handle_connect():
       if 'user' in session:
           username = session['user']
           users[request.sid] = username
           # Load previous messages from the database
           conn = sqlite3.connect('users.db')
           cur = conn.cursor()
           cur.execute("SELECT id, username, message, timestamp, message_type FROM messages ORDER BY timestamp")
           messages = cur.fetchall()
           conn.close()
           for msg in messages:
               emit('new_message', {
                   'id': msg[0],
                   'username': msg[1],
                   'content': msg[2],
                   'timestamp': msg[3],
                   'type': msg[4]
               }, room=request.sid)
           emit('user_joined', {'username': username}, broadcast=True)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        
        conn = None
        try:
            # Use context manager for auto-commit/rollback and connection cleanup
            with sqlite3.connect('users.db', timeout=20) as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO users (username, email, password) VALUES (?, ?,?)",
                    (username, email, password)
                )
                conn.commit()  # Explicit commit (optional but good practice)
                flash("Registered successfully! Please log in.")
                return redirect(url_for('login'))

        except sqlite3.IntegrityError:
            flash('Username already exists')
            return redirect(url_for('register'))
        
        except Exception as e:
            flash(f'Registration failed: {str(e)}')
            return redirect(url_for('register'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
	if request.method =="POST":
		username = request.form['username']
		password = request.form['password']
		
		conn = None
		try:
			conn = sqlite3.connect('users.db', timeout =20)
			cur = conn.cursor()
			cur.execute('SELECT password FROM users WHERE username = ?', (username,))
			user = cur.fetchone()
		finally:
			if conn:
				conn.close()
		
		if user and check_password_hash(user[0], password):  # Fixed variable name
		    session['user'] = username
		    return redirect(url_for('chat'))
		else:
			flash('Invalid credentials')
			return redirect(url_for('login'))
		
	return render_template('login.html')

@app.route('/chat')
def chat():
	if 'user' in session:
	    return render_template('chat.html', username=session['user'])
	else:
		return redirect(url_for('login'))
		
@socketio.on('disconnect')
def handle_disconnect():
		if request.sid in users:
			username = users[request.sid]
			del users[request.sid]
			emit('user_left', {'username': username, 'timestamp': time.time()}, broadcast=True)
			print(f'Client disconnected: {request.sid}')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'mp3', 'mp4'}
def allowed_file(mime_type):
	allowed ={'image/png', 'image/jpeg', 'image/gif', 'application/pdf', 'audio/mpeg', 'video/mp4'}
	return mime_type in allowed

@socketio.on('send_message')
def handle_message(data):
	if 'user' not in session:
		print("Error: User not authenticated")
		return
	username = session['user']
	if not username:
			return #Ignore messages from unauthenticated users
	try:
		message_content = data['content']
		message_type = data.get('type', 'text')
		timestamp = time.time()
		message_id = None
		
		if message_type != 'text':
			try:
				#Split data URL
				header,  encoded_content=data['content'].split(',', 1)
				#Get file extension
				content = base64.b64decode(encoded_content) #Decode here
				decoded_size = (len(encoded_content) * 3) // 4
				if decoded_size > 30 *1024 *1024:
					emit('error', {'message': 'File too large (max 30MB)'}, room=request.sid)
					return
				mime_type = header.split(';')[0].split(':')[1]
				if not allowed_file(mime_type):
					emit('error', {'message': 'File type not allowed'}, room=request.sid)
					return
				ext = mimetypes.guess_extension(mime_type) or '.bin'
				filename= secure_filename(f"{uuid.uuid4()}{ext}")
				file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
				os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok =True)
				with open(file_path, 'wb') as f:
				  	f.write(content)
				message_content = f"/uploads/{filename}"
			except Exception as e:
			   print(f"File processing failed: {str(e)}")
			   emit('error', {'message': 'File upload failed'}, room=request.sid)
			   return 
		
		with sqlite3.connect('users.db') as conn:
			try:
				cur = conn.cursor()
				cur.execute(
				"INSERT INTO messages (username, message, timestamp, message_type) VALUES (?, ?, ?, ?)",
				(username, message_content, timestamp, message_type)
				)
				message_id = cur.lastrowid #Get server generated id
				conn.commit()
			except Exception as e:
				print(f"Error for saving message: {e}")
				return 				
		if message_id: #Only emit if insertion succeed
			emit('new_message', {'id': message_id, #Include server id
			'username': username, 'content': message_content, 'type': message_type, 'timestamp': timestamp }, broadcast=True)
	except Exception as e:
		print(f"Critical error in send_message: {str(e)}") #Debug log

@socketio.on('delete_message')
def handle_delete(message_id):
    if 'user' not in session:
    	return
    username = session['user']
    conn = sqlite3.connect('users.db')
    try:
        cur = conn.cursor()
        cur.execute("SELECT username FROM messages WHERE id=?", (message_id,))
        result = cur.fetchone()
        if not result or result[0] != username:
        	return #Not authorized
        cur.execute("DELETE FROM messages WHERE id=?", (message_id,))
        conn.commit()
        emit('message_deleted', message_id, broadcast=True)
    finally:
        conn.close()

def cleanup_uploads():
	now = time.time()
	for f in os.listdir(app.config['UPLOAD_FOLDER']):
		filepath = os.path.join(app.config['UPLOAD_FOLDER'], f)
		if os.stat(filepath).st_mtime < now - 3600 * 24: #24hours old
		    os.remove(filepath)
	Timer(3600, cleanup_uploads).start() #Run hourly
	
@app.route('/logout',  methods=['POST'])
def logout():
	session.pop('user', None)
	flash('Logged out successfully.')
	return redirect(url_for('login'))
	
if (__name__) =='__main__':
	os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
	cleanup_uploads()
	app.run(port=9000, debug=True)