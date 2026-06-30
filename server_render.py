import os
import json
import asyncio
import subprocess
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import discord
from discord.ext import commands

# ================== إعدادات Flask و WebSocket ==================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ================== متغيرات عامة ==================
connected_clients = {}  # {client_id: {'sid': sid, 'name': name, 'ip': ip}}
client_responses = {}   # {client_id: response_data}
discord_queue = []
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', 'ضع_توكن_البوت_هنا')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', 123456789))

# ================== WebSocket للعملاء ==================
@socketio.on('connect')
def handle_connect():
    """عند اتصال عميل جديد"""
    print(f"[+] عميل متصل: {request.sid}")
    # العميل سيرسل اسمه بعد الاتصال مباشرة

@socketio.on('register')
def handle_register(data):
    """تسجيل العميل باسمه"""
    client_name = data.get('name', 'Unknown')
    connected_clients[client_name] = {
        'sid': request.sid,
        'name': client_name,
        'ip': request.remote_addr,
        'connected_at': datetime.now().isoformat()
    }
    print(f"[+] جهاز مسجل: {client_name}")
    send_to_discord(f"✅ الجهاز **{client_name}** متصل من {request.remote_addr}")
    emit('registered', {'status': 'success', 'message': f'مرحباً {client_name}'})

@socketio.on('command_result')
def handle_command_result(data):
    """استقبال نتيجة الأمر من العميل"""
    client_name = data.get('client_name')
    result = data.get('result')
    command_id = data.get('command_id')
    
    if client_name in connected_clients:
        # تخزين النتيجة لاسترجاعها لاحقاً
        client_responses[command_id] = result
        print(f"[+] نتيجة من {client_name}: {result[:50]}...")

@socketio.on('disconnect')
def handle_disconnect():
    """عند انقطاع العميل"""
    disconnected_name = None
    for name, info in connected_clients.items():
        if info['sid'] == request.sid:
            disconnected_name = name
            break
    if disconnected_name:
        del connected_clients[disconnected_name]
        send_to_discord(f"🔴 الجهاز **{disconnected_name}** غير متصل")
        print(f"[-] عميل غير متصل: {disconnected_name}")

# ================== ديسكورد بوت ==================
def send_to_discord(message):
    """إضافة رسالة إلى قائمة الانتظار للبوت"""
    discord_queue.append(message)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'[+] بوت ديسكورد متصل كـ {bot.user}')
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🖥️ **السيرفر على Render جاهز للعمل!**")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.channel.id != CHANNEL_ID:
        return

    content = message.content.strip()
    if not content:
        return

    # ===== أوامر البوت =====
    if content.startswith('!exec'):
        # !exec <اسم_الجهاز> <الأمر>
        parts = content.split(maxsplit=2)
        if len(parts) < 3:
            await message.channel.send("⚠️ استخدم: `!exec <اسم_الجهاز> <أمر>`")
            return
        
        _, client_name, command = parts
        
        if client_name not in connected_clients:
            await message.channel.send(f"❌ الجهاز **{client_name}** غير متصل")
            return
        
        # إرسال الأمر إلى العميل عبر WebSocket
        command_id = f"{client_name}_{datetime.now().timestamp()}"
        client_sid = connected_clients[client_name]['sid']
        
        try:
            # إرسال الأمر للعميل
            socketio.emit('execute_command', {
                'command': command,
                'command_id': command_id
            }, room=client_sid)
            
            # انتظار الرد (مهلة 30 ثانية)
            await asyncio.sleep(30)
            
            # استرجاع النتيجة
            result = client_responses.pop(command_id, "⏰ انتهت المهلة")
            
            if len(result) > 1900:
                result = result[:1900] + "\n... (مقطوع)"
            
            await message.channel.send(f"💻 **{client_name}** => `{command}`\n```\n{result}\n```")
            
        except Exception as e:
            await message.channel.send(f"❌ خطأ: {e}")
    
    elif content == '!list':
        if not connected_clients:
            await message.channel.send("❌ لا توجد أجهزة متصلة")
            return
        devices = "\n".join([f"- {name} (منذ {info['connected_at'][:16]})" 
                            for name, info in connected_clients.items()])
        await message.channel.send(f"📱 **الأجهزة المتصلة:**\n{devices}")
    
    elif content == '!help':
        help_text = """
**🤖 أوامر البوت:**
`!help` - عرض هذه المساعدة
`!list` - عرض الأجهزة المتصلة
`!exec <اسم_الجهاز> <أمر>` - تنفيذ أمر على جهاز معين

**📝 أمثلة:**
`!exec laptop dir`
`!exec desktop ipconfig`
`!exec raspberry python --version`
        """
        await message.channel.send(help_text)
    
    elif content == '!ping':
        await message.channel.send("🏓 Pong!")

# ================== تشغيل الخادم ==================
@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'clients': len(connected_clients),
        'message': '🚀 سيرفر التحكم عن بعد يعمل!'
    })

@app.route('/status')
def status():
    return jsonify({
        'connected_clients': list(connected_clients.keys()),
        'total': len(connected_clients)
    })

def run_socketio():
    """تشغيل سيرفر WebSocket"""
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# ================== التشغيل الرئيسي ==================
if __name__ == '__main__':
    # تشغيل بوت ديسكورد في خيط منفصل
    def run_bot():
        bot.run(DISCORD_TOKEN)
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # تشغيل خادم WebSocket
    run_socketio()
