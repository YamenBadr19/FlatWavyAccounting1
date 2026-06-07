"""
start_tunnel.py — تشغيل نفق ngrok لربط cTrader بـ Replit
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
شغّل هذا السكريبت على جهازك (حيث يعمل cTrader).
سيفتح نفقاً آمناً ويطبع الرابط الذي تضعه في Replit Secrets.

الاستخدام:
  pip install pyngrok requests
  python start_tunnel.py

أو مع توكن مباشر:
  python start_tunnel.py --token CoCmTefKw8XkZAFrYDo0G2U0U1AVT1S1BeMXCjJF7sI
"""

import argparse
import os
import sys
import time

def install_pyngrok():
    import subprocess
    print("  تثبيت pyngrok...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyngrok", "requests", "-q"])

try:
    from pyngrok import ngrok, conf
except ImportError:
    install_pyngrok()
    from pyngrok import ngrok, conf

import requests

CTRADER_PORT = 9876

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("NGROK_AUTH_TOKEN", ""))
    args = parser.parse_args()

    token = args.token.strip()
    if not token:
        token = input("أدخل ngrok Access Token: ").strip()

    if not token:
        print("❌ التوكن مطلوب")
        sys.exit(1)

    print()
    print("━" * 55)
    print("  Gold Blueprint — ngrok Tunnel Setup")
    print("━" * 55)
    print(f"  cTrader MCP Port: {CTRADER_PORT}")
    print()

    # إعداد التوكن
    conf.get_default().auth_token = token
    ngrok.set_auth_token(token)

    print("  جاري فتح النفق...")
    try:
        tunnel = ngrok.connect(CTRADER_PORT, "http")
        public_url = tunnel.public_url
    except Exception as e:
        print(f"❌ فشل فتح النفق: {e}")
        sys.exit(1)

    mcp_url = public_url.rstrip("/") + "/mcp/"

    print()
    print("━" * 55)
    print("  ✅ النفق يعمل!")
    print("━" * 55)
    print()
    print(f"  Public URL: {public_url}")
    print(f"  MCP URL:    {mcp_url}")
    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  أضف هذا في Replit Secrets:                 │")
    print("  │  Key:   MCP_URL                             │")
    print(f"  │  Value: {mcp_url:<37}│")
    print("  └─────────────────────────────────────────────┘")
    print()
    print("  ⚠️  أبقِ هذه النافذة مفتوحة — النفق سيتوقف عند إغلاقها")
    print()
    print("  اضغط Ctrl+C لإيقاف النفق")
    print()

    try:
        while True:
            time.sleep(30)
            # التحقق من أن النفق لا يزال يعمل
            try:
                r = requests.get(f"{public_url}/mcp/", timeout=5)
                status = "✅" if r.status_code < 500 else "⚠️"
            except requests.exceptions.ConnectionError:
                status = "❌ cTrader غير متصل"
            except Exception:
                status = "🔄"
            print(f"  [{time.strftime('%H:%M:%S')}] النفق: {status} | {mcp_url}")
    except KeyboardInterrupt:
        print()
        print("  إيقاف النفق...")
        ngrok.disconnect(tunnel.public_url)
        ngrok.kill()
        print("  تم إيقاف النفق.")


if __name__ == "__main__":
    main()
