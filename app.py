

import base64
import json
import logging
import queue
import re
import sys
import threading
import time
from datetime import datetime

import requests
import ddddocr
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)



class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q
    def emit(self, record):
        self.q.put({"type": "log", "level": record.levelname.lower(),
                    "text": self.format(record)})

log_queue: queue.Queue = queue.Queue()
log = logging.getLogger("sniper")
log.setLevel(logging.INFO)
_qh = QueueHandler(log_queue)
_qh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_qh)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_sh)


def push(event_type: str, **kwargs):
    log_queue.put({"type": event_type, **kwargs})




state = {
    "running":      False,
    "status":       "idle",
    "message":      "",
    "courses":      [],
    "user_choice":  None,
}
_sniper_thread: threading.Thread | None = None
_stop_event = threading.Event()
_active_session: requests.Session | None = None
_active_session_lock = threading.Lock()


def _is_running() -> bool:
    return bool(state.get("running")) and not _stop_event.is_set()


def _interruptible_sleep(seconds: float, step: float = 0.1) -> bool:
    end_time = time.time() + max(0.0, seconds)
    while time.time() < end_time:
        if not _is_running():
            return False
        remain = end_time - time.time()
        time.sleep(min(step, max(0.0, remain)))
    return True


def _register_active_session(sess: requests.Session):
    global _active_session
    with _active_session_lock:
        _active_session = sess


def _close_active_session():
    global _active_session
    with _active_session_lock:
        if _active_session is not None:
            try:
                _active_session.close()
            except Exception:
                pass
            _active_session = None



URP_CONFIG = {
    "base_url": "https://your-urp-host.example.edu.cn",
    "webvpn_auth": "https://your-webvpn-auth-host.example.edu.cn/authserver",
    "webvpn_base": "https://your-webvpn-urp-host.example.edu.cn",
    "cas_service": "https://your-webvpn-host.example.edu.cn/users/auth/cas",
}

BASE     = URP_CONFIG["base_url"]
UA       = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
NOT_OPEN = ("非选课时间", "不在选课时间", "选课未开始", "当前不允许", "未到选课时间", "非选课阶段")


WEBVPN_AUTH = URP_CONFIG["webvpn_auth"]
WEBVPN_BASE = URP_CONFIG["webvpn_base"]
CAS_SERVICE = URP_CONFIG["cas_service"]
WEBVPN_AUTH_ORIGIN = WEBVPN_AUTH.rsplit("/authserver", 1)[0]

UA_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.5 Mobile/15E148 Safari/604.1 Edg/146.0.0.0"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


_token = ""
_fajhh = ""
_xnxq  = ""
_login_retryable = True
_login_reason = ""


def _set_login_result(retryable: bool, reason: str = ""):
    global _login_retryable, _login_reason
    _login_retryable = retryable
    _login_reason = reason


def _analyze_jwxt_login_failure(resp_text: str, resp_url: str) -> tuple[bool, str]:
    text = re.sub(r"\s+", " ", (resp_text or ""))
    password_pats = (
        "用户名或密码错误", "账号或密码错误", "学号或密码错误", "密码错误", "用户名错误"
    )
    captcha_pats = (
        "验证码错误", "验证码不正确", "验证码有误", "captcha", "验证码"
    )

    if any(p in text for p in password_pats):
        return False, "教务登录失败：账号或密码错误"
    if any(p in text.lower() for p in captcha_pats) or "captcha" in resp_url.lower():
        return True, "教务登录失败：验证码识别失败，准备重试"
    return True, "教务登录失败：未知原因，准备重试"


def reset_runtime(use_webvpn: bool = False):
    global _token, _fajhh, _xnxq, BASE, UA
    _token = _fajhh = _xnxq = ""
    if use_webvpn:
        BASE = WEBVPN_BASE
        UA   = dict(UA_MOBILE)
    else:
        BASE = URP_CONFIG["base_url"]
        UA   = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        }


def set_token(t: str):
    global _token
    if t and t != _token:
        log.info(f"Token 刷新 → ...{t[-8:]}")
        _token = t


def ph(referer: str = "") -> dict:
    h = {**UA,
         "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
         "X-Requested-With": "XMLHttpRequest",
         "Accept": "application/json, text/javascript, */*; q=0.01"}
    if referer:
        h["Referer"] = referer
    return h




def get_captcha_b64(sess: requests.Session) -> tuple[str, bytes]:

    r = sess.get(f"{BASE}/img/captcha.jpg", headers=UA, timeout=10)
    r.raise_for_status()
    return base64.b64encode(r.content).decode(), r.content


def recognize_captcha(img_bytes: bytes) -> str:
    ocr = ddddocr.DdddOcr(show_ad=False)
    result = ocr.classification(img_bytes).strip()
    log.info(f"验证码识别: {result}")
    return result





_AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


def _random_str(length: int) -> str:
    import random
    return "".join(random.choice(_AES_CHARS) for _ in range(length))


def _first_group_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match and match.group(1):
            return match.group(1)
    return ""


def _aes_encrypt_password(password: str, salt: str) -> str:

    key       = salt.encode("utf-8").ljust(16, b"\x00")[:16]
    iv        = b"\x00" * 16

    prefix    = _random_str(64)
    plaintext = (prefix + password).encode("utf-8")
    cipher    = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(cipher.encrypt(pad(plaintext, 16))).decode()


def _get_login_page_params(sess, url):

    r    = sess.get(url, headers=UA_MOBILE, allow_redirects=True, timeout=10)

    final_url = r.url
    html = r.text
    result = {}
    soup = BeautifulSoup(html, "html.parser")

    tag = soup.find("input", {"name": "execution"})
    if tag and tag.get("value"):
        result["execution"] = tag["value"]

    for name in ("pwdEncryptSalt", "rsaPublicKey"):
        tag2 = soup.find("input", {"id": name}) or soup.find("input", {"name": name})
        if tag2 and tag2.get("value"):
            result["pub_key"] = tag2["value"]
            break

    if not result.get("execution"):
        execution_patterns = [
            r'(?:name|id)=["\']execution["\'][^>]*value=["\']([^"\']+)["\']',
            r'value=["\']([^"\']+)["\'][^>]*(?:name|id)=["\']execution["\']',
            r'"execution"\s*[:=]\s*"([^"]{8,})"',
            r"'execution'\s*[:=]\s*'([^']{8,})'",
        ]
        result["execution"] = _first_group_match(html, execution_patterns)

    if not result.get("pub_key"):
        salt_patterns = [
            r'(?:name|id)=["\']pwdEncryptSalt["\'][^>]*value=["\']([^"\']+)["\']',
            r'value=["\']([^"\']+)["\'][^>]*(?:name|id)=["\']pwdEncryptSalt["\']',
            r'(?:name|id)=["\']rsaPublicKey["\'][^>]*value=["\']([^"\']+)["\']',
            r'value=["\']([^"\']+)["\'][^>]*(?:name|id)=["\']rsaPublicKey["\']',
            r'"pwdEncryptSalt"\s*[:=]\s*"([^"]{8,64})"',
            r"'pwdEncryptSalt'\s*[:=]\s*'([^']{8,64})'",
        ]
        result["pub_key"] = _first_group_match(html, salt_patterns)
    result["final_url"] = final_url
    ex_info = "有(len=" + str(len(result["execution"])) + ")" if result.get("execution") else "无！"
    sk_info = "有(" + result["pub_key"][:8] + "...)" if result.get("pub_key") else "无"
    log.info("登录页参数: execution=" + ex_info + " salt=" + sk_info + " final_url=" + final_url[:60])
    return result


def _do_reauth(sess: requests.Session, reauth_url: str, password: str) -> bool:

    if not reauth_url.startswith("http"):
        reauth_url = f"{WEBVPN_AUTH_ORIGIN}{reauth_url}"
    log.info(f"二次认证页: {reauth_url[:80]}")
    params  = _get_login_page_params(sess, reauth_url)
    pub_key = params.get("pub_key", "")
    try:
        enc_pwd = _aes_encrypt_password(password, pub_key) if pub_key else password
    except Exception as ex:
        log.warning(f"二次认证 AES 加密失败({ex})，明文提交")
        enc_pwd = password
    r = sess.post(
        f"{WEBVPN_AUTH}/reAuthCheck/reAuthSubmit.do",
        data={"service":    f"{CAS_SERVICE}/callback?url",
              "reAuthType": "2",
              "password":   enc_pwd},
        headers={**UA_MOBILE,
                 "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                 "X-Requested-With": "XMLHttpRequest",
                 "Accept": "application/json, text/javascript, */*; q=0.01",
                 "Referer": reauth_url},
        timeout=15,
    )
    text = r.text.strip()
    log.info(f"二次认证响应: {text[:200]}")
    try:
        d = json.loads(text)

        code = str(d.get("code", "")).lower()
        msg = str(d.get("msg", ""))
        status = str(d.get("status", "")).lower()
        if (
            d.get("resultCode") in ("0", 0)
            or d.get("code") in ("0", 0)
            or d.get("success")
            or status == "success"
            or code in ("reauth_success", "success")
            or "成功" in msg
        ):
            log.info("二次认证成功")
            return True
        redirect = d.get("url") or d.get("redirectUrl") or d.get("location")
        if redirect:
            sess.get(redirect, headers=UA_MOBILE, allow_redirects=True, timeout=10)
            log.info("二次认证成功（跟随跳转）")
            return True
        log.error(f"二次认证失败: {d}")
        return False
    except Exception:
        if any(k in text for k in ("success", "成功", "redirect")):
            return True
        log.error(f"二次认证响应无法解析: {text[:100]}")
        return False


def do_login_webvpn(sess: requests.Session, username: str, password: str) -> bool:

    from urllib.parse import urljoin

    sess.headers.update(UA_MOBILE)
    login_url = f"{WEBVPN_AUTH}/login?service={CAS_SERVICE}/callback?url"

    push("status", status="polling", message="WebVPN：账号密码登录中...")
    log.info("WebVPN 使用手机 UA 进行账号密码登录")

    try:
        base_auth = WEBVPN_AUTH_ORIGIN
        ajax_h = {**UA_MOBILE, "X-Requested-With": "XMLHttpRequest", "Referer": base_auth + "/"}
        sess.get(base_auth, headers=UA_MOBILE, allow_redirects=True, timeout=10)
        sess.post(f"{base_auth}/authserver/common/getLanguageTypes.htl", data={}, headers=ajax_h, timeout=10)
        sess.get(f"{base_auth}/authserver/tenant/info", headers=ajax_h, timeout=10)
        sess.post(f"{base_auth}/authserver/common/getLanguageTypes.htl", data={}, headers=ajax_h, timeout=10)
    except Exception as ex:
        log.warning(f"WebVPN 会话预热失败(可忽略): {ex}")

    params = _get_login_page_params(sess, login_url)
    execution = params.get("execution", "")
    salt = params.get("pub_key", "")
    post_url = params.get("final_url", login_url)

    if not execution:
        log.error("WebVPN 登录失败：未获取 execution")
        return False

    def _submit(ex_value: str, salt_value: str):
        try:
            enc_pwd = _aes_encrypt_password(password, salt_value) if salt_value else password
        except Exception as ex:
            log.warning(f"WebVPN AES 加密失败({ex})，使用明文提交")
            enc_pwd = password

        data = {
            "username": username,
            "password": enc_pwd,
            "captcha": "",
            "_eventId": "submit",
            "lt": "",
            "cllt": "userNameLogin",
            "dllt": "generalLogin",
            "execution": ex_value,
        }
        headers = {
            **UA_MOBILE,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": post_url,
            "Origin": WEBVPN_AUTH_ORIGIN,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        return sess.post(post_url, data=data, headers=headers, allow_redirects=False, timeout=15)

    r = _submit(execution, salt)

    if r.status_code == 200:
        params2 = _get_login_page_params(sess, post_url)
        execution2 = params2.get("execution", "")
        salt2 = params2.get("pub_key", "")
        if execution2 and execution2 != execution:
            log.info("WebVPN 首次提交返回 200，使用新 execution/salt 重试")
            r = _submit(execution2, salt2)

    if r.status_code not in (301, 302):
        tip = re.sub(r"\s+", " ", r.text)[:160]
        log.error(f"WebVPN 登录失败: HTTP {r.status_code} {tip}")
        return False

    landing_url = getattr(r, "url", "")
    location = r.headers.get("Location", "")
    if location:
        next_url = urljoin(post_url, location)
        r_next = sess.get(next_url, headers=UA_MOBILE, allow_redirects=True, timeout=15)
        landing_url = r_next.url

    if "reAuthCheck" in landing_url or "reAuthLoginView" in landing_url:
        if not _do_reauth(sess, landing_url, password):
            return False

    try:
        probe = sess.get(f"{WEBVPN_BASE}/login", headers=UA_MOBILE, allow_redirects=True, timeout=10)
        if probe.status_code != 200:
            log.error(f"WebVPN 通道建立后访问教务登录页失败: HTTP {probe.status_code}")
            return False
    except Exception as ex:
        log.error(f"WebVPN 通道建立后访问教务登录页异常: {ex}")
        return False

    log.info("WebVPN 通道已建立，准备复用教务登录流程")
    return True




def do_login(sess: requests.Session, username: str, password: str,
              use_webvpn: bool = False) -> bool:
    if use_webvpn:
        log.info("通过 WebVPN 登录...")
        if not do_login_webvpn(sess, username, password):
            _set_login_result(False, "WebVPN 认证失败，请检查 WebVPN 账号密码或网络")
            return False
        log.info("WebVPN 已就绪，复用教务登录流程...")
    else:
        log.info("直连登录...")

    page = sess.get(f"{BASE}/login", headers=UA, timeout=10)
    m = (re.search(r'(?:name|id)=["\'\']tokenValue["\'\'][^>]*value=["\'\']([^"\'\']{10,})["\'\']', page.text)
         or re.search(r'value=["\'\']([^"\'\']{10,})["\'\'][^>]*(?:name|id)=["\'\']tokenValue["\'\']', page.text))
    tok = m.group(1) if m else ""

    cap_b64, cap_bytes = get_captcha_b64(sess)
    push("captcha", image=cap_b64)
    cap_text = recognize_captcha(cap_bytes)

    r = sess.post(
        f"{BASE}/j_spring_security_check",
        data={"lang": "zh", "tokenValue": tok,
              "j_username": username, "j_password": password,
              "j_captcha": cap_text},
        headers={**UA, "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": f"{BASE}/login"},
        allow_redirects=True, timeout=15,
    )
    ok = "login" not in r.url and "j_spring_security_check" not in r.url
    if ok:
        _set_login_result(True, "")
        log.info("登录成功")
    else:
        retryable, reason = _analyze_jwxt_login_failure(r.text, r.url)
        _set_login_result(retryable, reason)
        log.error(reason)
    return ok



def poll_until_open(sess: requests.Session) -> bool:
    global _fajhh, _xnxq, _token
    url = f"{BASE}/student/courseSelect/courseSelect/index"
    n = 0
    while _is_running():
        n += 1
        try:
            r = sess.get(url, headers=UA, timeout=8)
            if "login" in r.url:
                log.warning("Session 失效，请重启程序重新登录")
                return False
            m = re.search(r'fajhh=(\d+)', r.text)
            if not m:
                if n % 20 == 1:
                    msg = f"选课系统尚未开放，轮询中... ({n}次)"
                    log.info(msg)
                    push("status", status="polling", message=msg)
                if not _interruptible_sleep(0.5):
                    return False
                continue
            _fajhh = m.group(1)
            mt = (re.search(r'id=["\']tokenValue["\'][^>]*value=["\']([a-f0-9]{32})["\']', r.text)
                  or re.search(r'value=["\']([a-f0-9]{32})["\'][^>]*id=["\']tokenValue["\']', r.text))
            if mt:
                set_token(mt.group(1))
            _xnxq = fetch_xnxq(sess)
            log.info(f"系统已开放！fajhh={_fajhh}  xnxq={_xnxq}")
            return True
        except requests.RequestException as e:
            log.warning(f"轮询异常: {e}")
            if not _interruptible_sleep(0.5):
                return False
    return False


def fetch_xnxq(sess: requests.Session) -> str:
    try:
        r = sess.post(f"{BASE}/main/checkSelectCourseStatus",
                      data={}, headers=ph(f"{BASE}/"), timeout=8)
        d = r.json()
        zx = d.get("zxjxjhm", "")
        m = re.search(r'(\d{4}-\d{4})学年([春秋夏])', zx)
        if m:
            s = {"春": "2", "秋": "1", "夏": "3"}.get(m.group(2), "1")
            return f"{m.group(1)}-{s}-1"
    except Exception:
        pass
    return ""




def search_courses(sess: requests.Session,
                   kch="", kcm="", skjs="", kxh="") -> list | None:

    ref = f"{BASE}/student/courseSelect/freeCourse/index?fajhh={_fajhh}&fj=0"
    sess.get(ref, headers=UA, timeout=8)
    r = sess.post(
        f"{BASE}/student/courseSelect/freeCourse/courseList",
        data={"kkxsh": "", "kch": kch, "kcm": kcm, "skjs": skjs,
              "xq": "0", "jc": "0", "kclbdm": "", "kclbdm2": "", "vt": "", "fj": "0"},
        headers=ph(ref), timeout=15,
    )
    if r.status_code != 200:
        return None
    text = r.text.strip()
    if any(k in text for k in NOT_OPEN):
        return None
    try:
        raw = r.json()
    except Exception:
        log.warning(f"courseList 非 JSON: {text[:200]}")
        return None

    items = (raw if isinstance(raw, list)
             else raw.get("rwRxkZlList", raw.get("list", raw.get("data", []))))
    if not isinstance(items, list):
        return []

    if kxh:
        items = [c for c in items
                 if str(c.get("kxh", "")).strip() == kxh.strip()]

    global _xnxq
    if not _xnxq and items:
        v = items[0].get("zxjxjhh", "")
        if v:
            _xnxq = v

    return items


def build_kc_id(c: dict) -> str:
    return f"{c.get('kch','')}_{c.get('kxh','')}_{c.get('zxjxjhh', _xnxq)}"


def _kc_id_to_event(kc_id: str, kc_id_map: dict) -> dict:

    c    = kc_id_map.get(kc_id, {})

    parts = kc_id.split("_")
    kch   = parts[0] if len(parts) > 0 else c.get("kch", "")
    kxh   = parts[1] if len(parts) > 1 else c.get("kxh", "")
    return {
        "kch":  kch,
        "kxh":  kxh,
        "kcm":  c.get("kcm",  ""),
        "skjs": c.get("skjs", "").strip(),
    }




def resolve_courses(sess: requests.Session, target_list: list) -> list:
    confirmed = []
    for idx, cond in enumerate(target_list, 1):
        kch  = cond.get("kch",  "").strip()
        kcm  = cond.get("kcm",  "").strip()
        skjs = cond.get("skjs", "").strip()
        kxh  = cond.get("kxh",  "").strip()
        n = 0

        while _is_running():
            n += 1
            results = search_courses(sess, kch=kch, kcm=kcm, skjs=skjs, kxh=kxh)

            if results is None:
                if n % 20 == 1:
                    log.info(f"[课程{idx}] 系统未开放，轮询中... ({n}次)")
                if not _interruptible_sleep(0.5):
                    return []
                continue

            if len(results) == 0:
                log.error(f"[课程{idx}] 搜索无结果！条件: {cond}")
                push("status", status="failed",
                     message=f"课程{idx}搜索无结果，请检查配置后重新启动")
                state["running"] = False
                return []

            if len(results) == 1:
                c = results[0]
                log.info(f"[课程{idx}] 自动锁定: {c.get('kcm','')} "
                         f"{c.get('kch','')}_{c.get('kxh','')} "
                         f"教师: {c.get('skjs','').strip()}")
                confirmed.append(c)
                break


            log.info(f"[课程{idx}] 找到 {len(results)} 个结果，等待选择...")
            push("choice_required",
                 course_idx=idx,
                 courses=[{
                     "kch":    c.get("kch", ""),
                     "kxh":    c.get("kxh", ""),
                     "kcm":    c.get("kcm", ""),
                     "skjs":   c.get("skjs", "").strip(),
                     "bkskyl": c.get("bkskyl", "?"),
                     "bkskrl": c.get("bkskrl", "?"),
                 } for c in results])


            for _ in range(240):
                if not _is_running():
                    return []
                choice = state.get("user_choice")
                if choice and choice.get("course_idx") == idx:
                    state["user_choice"] = None
                    n_choice = choice.get("choice", 0)
                    if n_choice == 0:
                        log.info(f"[课程{idx}] 已跳过")
                    else:
                        c = results[n_choice - 1]
                        confirmed.append(c)
                        log.info(f"[课程{idx}] 用户选择: "
                                 f"{c.get('kcm','')} {c.get('kch','')}_{c.get('kxh','')}")
                    break
                if not _interruptible_sleep(0.5):
                    return []
            else:
                log.warning(f"[课程{idx}] 等待用户选择超时，已跳过")
            break

    return confirmed




def step1(sess: requests.Session, kc_ids: list, kcms: str) -> str:
    r = sess.post(
        f"{BASE}/student/courseSelect/selectCourse/checkInputCodeAndSubmit",
        data={"dealType": "5", "kcIds": ",".join(kc_ids), "kcms": kcms,
              "fajhh": _fajhh, "fj": "0", "sj": "0_0",
              "kkxsh": "", "kclbdm": "", "inputCode": "undefined",
              "tokenValue": _token},
        headers=ph(f"{BASE}/student/courseSelect/courseSelect/index"),
        timeout=15,
    )
    text = r.text.strip()
    log.info(f"Step1 [{r.status_code}]: {text[:200]}")
    try:
        d = json.loads(text)
        if isinstance(d, dict) and "token" in d:
            set_token(d["token"])
    except Exception:
        pass
    return text


def step2(sess: requests.Session, kc_ids: list, kcms: str,
          username: str) -> tuple[str, int]:
    ts = int(time.time() * 1000)
    r  = sess.post(
        f"{BASE}/student/courseSelect/selectCourses/waitingfor",
        data={"dealType": "5", "kcIds": ",".join(kc_ids), "kcms": kcms,
              "fajhh": _fajhh, "fj": "0", "sj": "0_0", "kkxsh": "", "kclbdm": ""},
        headers={**UA, "Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "text/html,*/*",
                 "Referer": f"{BASE}/student/courseSelect/freeCourse/index"
                            f"?fajhh={_fajhh}&fj=0"},
        timeout=15,
    )
    html = r.text
    mk = re.search(r'(?:redisKey|redis_key)\s*[=:]\s*["\']?([A-Za-z0-9_:]+)["\']?', html)
    mn = re.search(r'kcNum\s*[=:]\s*["\']?(\d+)["\']?', html)
    kc_num    = int(mn.group(1)) if mn else len(kc_ids)
    redis_key = mk.group(1) if mk else f"{username}{kc_num}:{ts}"
    if not mk:
        log.warning(f"redisKey 构造: {redis_key}")
    else:
        log.info(f"redisKey={redis_key}  kcNum={kc_num}")
    return redis_key, kc_num



RETRY_MSGS = ("没有课余量", "课余量不足", "人数已满", "选课人数已满", "已满")

STOP_MSGS  = ("已经选择了课程", "选课成功", "超过了该课程课组", "超过最高门数",
              "冲突", "不能选择", "不允许", "已超出")


def query_result(sess: requests.Session,
                 redis_key: str, kc_num: int) -> tuple[str, str | dict]:

    ref = f"{BASE}/student/courseSelect/selectCourses/waitingfor"

    for i in range(1, 21):
        if not _is_running():
            return "stopped", "任务已手动停止"
        try:
            r = sess.post(f"{BASE}/student/courseSelect/selectResult/query",
                          data={"kcNum": kc_num, "redisKey": redis_key},
                          headers=ph(ref), timeout=10)
            text = r.text.strip()
            log.info(f"查询 [{i}/20]: {text[:300]}")
            if not text:
                if not _interruptible_sleep(1.2):
                    return "stopped", "任务已手动停止"
                continue


            try:
                d = json.loads(text)
            except Exception:

                if any(k in text for k in ("等待", "排队", "processing")):
                    if not _interruptible_sleep(1.2):
                        return "stopped", "任务已手动停止"
                    continue
                if "成功" in text:
                    return "success", text
                if i >= 3:
                    return "unknown", text
                if not _interruptible_sleep(1.2):
                    return "stopped", "任务已手动停止"
                continue


            if not d.get("isFinish", True):
                if not _interruptible_sleep(1.2):
                    return "stopped", "任务已手动停止"
                continue

            result_list = d.get("result", [])


            if not result_list:
                if not _interruptible_sleep(1.2):
                    return "stopped", "任务已手动停止"
                continue


            need_retry   = []
            done_ok      = []
            done_blocked = []

            for item_raw in result_list:
                if ':' not in item_raw:
                    continue
                key, _, msg = item_raw.partition(':')
                key = key.strip(); msg = msg.strip()

                if any(k in msg for k in ("选课成功",)):
                    log.info(f"  ✅ {key}: {msg}")
                    done_ok.append(key)
                elif any(k in msg for k in ("已经选择了课程",)):
                    log.info(f"  ✅ {key}: {msg}（已选）")
                    done_ok.append(key)
                elif any(k in msg for k in RETRY_MSGS):
                    log.warning(f"  🔄 {key}: {msg}（将重试）")
                    need_retry.append(key)
                elif any(k in msg for k in STOP_MSGS):
                    log.warning(f"  ❌ {key}: {msg}（跳过）")
                    done_blocked.append(key)
                else:

                    log.info(f"  ❓ {key}: {msg}（未知，将重试）")
                    need_retry.append(key)


            summary = (f"成功/已选 {len(done_ok)} 门，"
                       f"需重试 {len(need_retry)} 门，"
                       f"永久失败 {len(done_blocked)} 门")
            log.info(f"本轮结果: {summary}")

            if need_retry:

                return "fail", {
                    "need_retry":   need_retry,
                    "done_ok":      done_ok,
                    "done_blocked": done_blocked,
                }


            if done_ok:
                return "success", {"done_ok": done_ok, "done_blocked": done_blocked,
                                   "summary": summary}
            else:
                return "blocked", {"done_ok": [], "done_blocked": done_blocked,
                                   "summary": summary}

        except Exception as e:
            log.warning(f"查询异常: {e}")
        if not _interruptible_sleep(1.2):
            return "stopped", "任务已手动停止"

    return "timeout", "查询超时，请手动确认"




def sniper_main(config: dict):
    global state, _login_retryable, _login_reason
    username       = config["username"]
    password       = config["password"]
    start_time_str = config.get("start_time", "").strip()
    target_list    = config["courses"]
    retry_interval = float(config.get("retry_interval", 0.8))
    use_webvpn     = config.get("use_webvpn", False)

    reset_runtime(use_webvpn)
    sess = requests.Session()
    _register_active_session(sess)

    try:

        mode_label = "WebVPN" if use_webvpn else "直连"
        push("status", status="polling", message=f"正在登录（{mode_label}）...")

        if use_webvpn:
            log.info("通过 WebVPN 登录...")
            if not do_login_webvpn(sess, username, password):
                _set_login_result(False, "WebVPN 认证失败，请检查 WebVPN 账号密码或网络")
                msg = _login_reason or "WebVPN 认证失败"
                push("status", status="failed", message=msg)
                state["running"] = False
                return
            log.info("WebVPN 已就绪，开始教务系统登录（仅重试教务部分）...")

        for attempt in range(1, 6):
            if not _is_running():
                return
            try:

                if do_login(sess, username, password, use_webvpn=False):
                    break
                if not _login_retryable:
                    msg = _login_reason or "登录失败，请检查账号密码后重启"
                    push("status", status="failed", message=msg)
                    state["running"] = False
                    return
            except Exception as e:
                log.warning(f"登录异常({attempt}/5): {e}")
            if attempt == 5:
                final_msg = _login_reason or "登录失败，请检查账号密码后重启"
                push("status", status="failed", message=final_msg)
                state["running"] = False
                return
            if not _interruptible_sleep(2):
                return


        if start_time_str:
            target_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
            while _is_running():
                diff = (target_dt - datetime.now()).total_seconds()
                if diff <= 0:
                    log.info("时间到！开始抢课")
                    break
                m, s = int(diff // 60), int(diff % 60)
                msg = f"距开抢还有 {m}分{s}秒"
                push("status", status="waiting", message=msg)
                log.info(msg)
                if not _interruptible_sleep(min(10, max(0.5, diff - 0.5))):
                    return


        push("status", status="polling", message="等待选课系统开放...")
        if not poll_until_open(sess):
            state["running"] = False
            return


        push("status", status="searching", message="搜索目标课程...")
        confirmed = resolve_courses(sess, target_list)
        if not confirmed or not state["running"]:
            state["running"] = False
            return


        state["courses"] = [{"kcm": c.get("kcm",""), "kch": c.get("kch",""),
                              "kxh": c.get("kxh",""), "skjs": c.get("skjs","").strip()}
                            for c in confirmed]
        push("courses_locked", courses=state["courses"])



        kc_id_map = {build_kc_id(c): c for c in confirmed}
        kc_ids    = list(kc_id_map.keys())
        kcms      = ",".join(str(c.get("kcms", "")) for c in confirmed)
        log.info(f"准备提交 {len(kc_ids)} 门课: {kc_ids}")


        push("status", status="sniping", message=f"正在抢 {len(kc_ids)} 门课...")
        retry = 0
        while _is_running():
            retry += 1
            log.info(f"[第{retry}次] {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            try:
                if not _is_running():
                    return
                s1 = step1(sess, kc_ids, kcms)
                if any(k in s1 for k in NOT_OPEN):
                    log.warning("Step1 仍返回未开放，继续轮询...")
                    if not _interruptible_sleep(0.5):
                        return
                    continue

                redis_key, kc_num = step2(sess, kc_ids, kcms, username)
                result, payload   = query_result(sess, redis_key, kc_num)

                if result == "stopped":
                    return

                if result in ("success", "blocked", "fail"):
                    done_ok_keys      = payload.get("done_ok",      [])
                    done_blocked_keys = payload.get("done_blocked",  [])
                    summary           = payload.get("summary",       "")


                    if done_ok_keys:
                        done_events = [
                            _kc_id_to_event(kid, kc_id_map)
                            for kid in kc_ids
                            if any(kid.startswith(k) for k in done_ok_keys)
                        ]
                        log.info(f"推送 courses_done: {done_events}")
                        push("courses_done", courses=done_events)

                    if done_blocked_keys:
                        failed_events = [
                            _kc_id_to_event(kid, kc_id_map)
                            for kid in kc_ids
                            if any(kid.startswith(k) for k in done_blocked_keys)
                        ]
                        log.info(f"推送 courses_failed: {failed_events}")
                        push("courses_failed", courses=failed_events)

                    if result == "success":
                        log.info(f"🎉 {summary}")
                        push("status", status="success", message=f"🎉 {summary}")
                        state["running"] = False
                        return

                    if result == "blocked":
                        log.error(f"❌ {summary}")
                        push("status", status="failed", message=summary)
                        state["running"] = False
                        return


                    completed_keys = done_ok_keys + done_blocked_keys
                    new_kc_ids = [kid for kid in kc_ids
                                  if not any(kid.startswith(k) for k in completed_keys)]
                    if not new_kc_ids:
                        log.info("所有课程已处理完毕")
                        push("status", status="success", message=f"抢课完成！{summary}")
                        state["running"] = False
                        return
                    if len(new_kc_ids) < len(kc_ids):
                        kc_ids = new_kc_ids
                        kcms   = ",".join(str(kc_id_map[k].get("kcms","")) for k in kc_ids)
                        log.info(f"剩余待抢: {kc_ids}")
                        push("status", status="sniping",
                             message=f"仍在抢 {len(kc_ids)} 门课...")
                    else:
                        log.warning("本次全部需重试，继续...")
                else:
                    log.info(f"结果未知({result})，继续重试...")

            except requests.ConnectionError as e:
                log.warning(f"连接错误: {e}")
            except requests.Timeout:
                log.warning("请求超时，重试中...")
            except Exception as e:
                log.error(f"异常: {e}", exc_info=True)

            if not _interruptible_sleep(retry_interval):
                return

    except Exception as e:
        if _stop_event.is_set():
            log.info("任务已手动停止")
        else:
            log.error(f"任务异常终止: {e}", exc_info=True)
            push("status", status="failed", message=str(e))
    finally:
        _close_active_session()
        state["running"] = False




@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def api_start():
    global _sniper_thread
    if state["running"]:
        return jsonify(ok=False, msg="任务已在运行中"), 400

    cfg = request.json or {}
    if not cfg.get("username") or not cfg.get("password"):
        return jsonify(ok=False, msg="请填写学号和密码"), 400
    if not cfg.get("courses"):
        return jsonify(ok=False, msg="请至少添加一门课程"), 400
    for i, c in enumerate(cfg["courses"], 1):
        if not any(c.get(k, "").strip() for k in ("kch", "kcm", "skjs", "kxh")):
            return jsonify(ok=False, msg=f"课程{i}：至少填写一个搜索条件"), 400


    _stop_event.clear()
    state.update(running=True, status="polling", message="启动中...",
                 courses=[], user_choice=None)
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break

    _sniper_thread = threading.Thread(target=sniper_main, args=(cfg,), daemon=True)
    _sniper_thread.start()
    return jsonify(ok=True)


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["running"] = False
    _stop_event.set()
    _close_active_session()
    push("status", status="idle", message="已手动停止")
    return jsonify(ok=True)


@app.route("/api/choose", methods=["POST"])
def api_choose():

    state["user_choice"] = request.json
    return jsonify(ok=True)


@app.route("/api/status")
def api_status():
    return jsonify(running=state["running"], status=state["status"],
                   message=state["message"], courses=state["courses"])


@app.route("/stream")
def stream():

    def gen():

        yield f"data: {json.dumps({'type':'status','status':state['status'],'message':state['message']}, ensure_ascii=False)}\n\n"
        while True:
            try:
                item = log_queue.get(timeout=25)
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": ping\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    print("=" * 50)
    print("  URP 抢课助手已启动")
    print("  请在浏览器打开 http://127.0.0.1:5000")
    print("=" * 50)
    app.run(debug=False, threaded=True, port=5000)


