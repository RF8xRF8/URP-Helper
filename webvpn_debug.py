
import requests
import base64
import re
import json
import random
from bs4 import BeautifulSoup

USERNAME = "YOUR_URP_USERNAME"
PASSWORD = "YOUR_URP_PASSWORD"
ENCRYPT_MODE = "js_zero_iv"

WEBVPN_CONFIG = {
    "webvpn_auth": "https://your-webvpn-auth-host.example.edu.cn/authserver",
    "cas_service": "https://your-webvpn-host.example.edu.cn/users/auth/cas",
}

WEBVPN_AUTH = WEBVPN_CONFIG["webvpn_auth"]
CAS_SERVICE = WEBVPN_CONFIG["cas_service"]
LOGIN_URL    = f"{WEBVPN_AUTH}/login?service={CAS_SERVICE}/callback?url"

UA_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.5 Mobile/15E148 Safari/604.1 Edg/146.0.0.0"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

sess = requests.Session()
sess.headers.update(UA_MOBILE)

AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


def random_string_like_js(length: int) -> str:
    return "".join(random.choice(AES_CHARS) for _ in range(length))


BASE_AUTH = WEBVPN_AUTH.rsplit("/authserver", 1)[0]
print(">>> 初始化 session...")
sess.get(BASE_AUTH, allow_redirects=True)
ajax_h = {**UA_MOBILE, "X-Requested-With": "XMLHttpRequest", "Referer": BASE_AUTH + "/"}
sess.post(f"{BASE_AUTH}/authserver/common/getLanguageTypes.htl", data={}, headers=ajax_h)
sess.get(f"{BASE_AUTH}/authserver/tenant/info", headers=ajax_h)
sess.post(f"{BASE_AUTH}/authserver/common/getLanguageTypes.htl", data={}, headers=ajax_h)
print(f"初始化完成，cookies: {dict(sess.cookies)}")

def dump(label, r):
    print(f"\n{'='*60}")
    print(f"[{label}]")
    print(f"URL:    {r.url}")
    print(f"Status: {r.status_code}")
    print(f"Headers sent:")
    for k, v in r.request.headers.items():
        print(f"  {k}: {v[:120]}")
    if r.request.body:
        body = r.request.body
        if isinstance(body, bytes):
            body = body.decode('utf-8', 'replace')

        body_show = re.sub(r'(password=)[^&]+', r'\1***', body)
        print(f"Body:   {body_show[:300]}")
    print(f"Response Headers:")
    for k, v in r.headers.items():
        print(f"  {k}: {v[:120]}")
    print(f"Cookies after this request:")
    for k, v in sess.cookies.items():
        print(f"  {k}={v[:40]}")
    print(f"{'='*60}")


def encrypt_password_like_js(password: str, salt: str) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    key = salt.encode("utf-8").ljust(16, b"\x00")[:16]
    iv = b"\x00" * 16
    plaintext = (random_string_like_js(64) + password).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(cipher.encrypt(pad(plaintext, 16))).decode()


def encrypt_password_js_random_iv(password: str, salt: str) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    key = salt.encode("utf-8").ljust(16, b"\x00")[:16]
    iv = random_string_like_js(16).encode("utf-8")
    plaintext = (random_string_like_js(64) + password).encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(cipher.encrypt(pad(plaintext, 16))).decode()


print("\n>>> Step 1: GET 登录页")
r1 = sess.get(LOGIN_URL, allow_redirects=True)
dump("GET login", r1)


soup = BeautifulSoup(r1.text, "html.parser")
print("\n>>> 登录页所有 input 字段:")
for inp in soup.find_all("input"):
    name  = inp.get("name", "")
    id_   = inp.get("id", "")
    type_ = inp.get("type", "")
    val   = inp.get("value", "")
    if len(val) > 60:
        val = val[:20] + f"...(len={len(val)})"
    print(f"  name={name!r:20} id={id_!r:20} type={type_!r:12} value={val!r}")

print("\n>>> 登录页所有 form:")
for form in soup.find_all("form"):
    print(f"  id={form.get('id')!r} action={form.get('action')!r} method={form.get('method')!r}")


execution = ""
pub_key   = ""
tag = soup.find("input", {"name": "execution"})
if tag:
    execution = tag.get("value", "")
for n in ("pwdEncryptSalt", "rsaPublicKey"):
    t = soup.find("input", {"id": n}) or soup.find("input", {"name": n})
    if t:
        pub_key = t.get("value", "")
        break

print(f"\n>>> execution: {'有 len=' + str(len(execution)) if execution else '无！'}")
print(f">>> pub_key:   {pub_key!r}")
print(f">>> final URL: {r1.url}")

if not execution:
    print("\n❌ 无法提取 execution，退出")
    exit(1)


print("\n>>> Step 2: AES 加密密码")
try:
    if ENCRYPT_MODE == "plain":
        enc_pwd = PASSWORD
    elif ENCRYPT_MODE == "js_random_iv":
        enc_pwd = encrypt_password_js_random_iv(PASSWORD, pub_key)
    else:
        enc_pwd = encrypt_password_like_js(PASSWORD, pub_key)
    print(f"加密成功，密文长度: {len(enc_pwd)}")
    print(f"当前加密模式: {ENCRYPT_MODE}")
except Exception as e:
    print(f"AES 失败: {e}，使用明文")
    enc_pwd = PASSWORD


print("\n>>> Step 3: POST 登录")
post_data = {
    "username":  USERNAME,
    "password":  enc_pwd,
    "captcha":   "",
    "_eventId":  "submit",
    "lt":        "",
    "cllt":      "userNameLogin",
    "dllt":      "generalLogin",
    "execution": execution,
}

print(f"POST前 cookies: {dict(sess.cookies)}")
print("保留 Cookie，准备 POST")

r2 = sess.post(
    r1.url,
    data=post_data,
    headers={
        "Content-Type":              "application/x-www-form-urlencoded",
        "Referer":                   r1.url,
        "Origin":                    BASE_AUTH,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "same-origin",
        "Sec-Fetch-User":            "?1",
        "Cache-Control":             "max-age=0",
    },
    allow_redirects=False,
)
dump("POST login", r2)

if r2.status_code in (301, 302):
    print(f"\n✅ 302 跳转成功！Location: {r2.headers.get('Location','')}")
else:
    print(f"\n❌ 预期302，收到 {r2.status_code}")

    soup2 = BeautifulSoup(r2.text, "html.parser")
    text2 = re.sub(r'\s+', ' ', soup2.get_text()).strip()
    print(f"页面文字: {text2[:500]}")
    with open("webvpn_debug2.html", "w", encoding="utf-8") as f:
        f.write(r2.text)
    print("响应已写入 webvpn_debug2.html")


if r2.status_code == 200:
    print("\n>>> 200响应里提取新 execution 重试...")
    from bs4 import BeautifulSoup as BS
    soup2 = BS(r2.text, "html.parser")
    tag2  = soup2.find("input", {"name": "execution"})
    ex2   = tag2.get("value","") if tag2 else ""
    salt2 = ""
    for n in ("pwdEncryptSalt","rsaPublicKey"):
        t = soup2.find("input",{"id":n}) or soup2.find("input",{"name":n})
        if t and t.get("value"):
            salt2 = t["value"]; break

    print(f"新 execution len={len(ex2)}  新 salt={salt2[:8] if salt2 else '无'}")

    if ex2 and ex2 != execution:

        try:
            if ENCRYPT_MODE == "plain":
                enc2 = PASSWORD
            elif ENCRYPT_MODE == "js_random_iv":
                enc2 = encrypt_password_js_random_iv(PASSWORD, salt2)
            else:
                enc2 = encrypt_password_like_js(PASSWORD, salt2)
        except:
            enc2 = PASSWORD

        r3 = sess.post(r1.url, data={
            "username": USERNAME, "password": enc2,
            "_eventId":"submit","cllt":"userNameLogin",
            "dllt":"generalLogin","execution": ex2
        }, headers={"Content-Type":"application/x-www-form-urlencoded",
                    "Referer":r1.url,"Origin":BASE_AUTH},
        allow_redirects=False)
        dump("POST login (retry)", r3)
        if r3.status_code in (301,302):
            print(f"\n✅ 重试成功！Location: {r3.headers.get('Location','')}")
        else:
            print(f"\n❌ 重试仍然失败: {r3.status_code}")


