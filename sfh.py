import requests
from bs4 import BeautifulSoup
import json
import os
import asyncio
import aiohttp
from urllib.parse import urljoin
from collections import defaultdict

# Конфигурация
AUTH_FILE = "auth.json"
BASE_URL = "https://system.fgoupsk.ru"
LOGIN_URL = urljoin(BASE_URL, "/student/login")
PAGE_URL = urljoin(BASE_URL, "/student/?mode=ucheba&act=group&act2=prog&m={m}&d={d}")


def load_auth():
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ошибка чтения {AUTH_FILE}: {e}")
    return None


def save_auth(data):
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"💾 Учётные данные сохранены в {AUTH_FILE}")


def create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": BASE_URL,
        "Referer": urljoin(BASE_URL, "/student/"),
    })
    return session


def login(session, creds):
    print("🔐 Авторизация...")
    user_id = creds.get("id") or creds.get("login") or ""
    password = creds.get("password") or creds.get("pass") or ""
    payload = {"id": user_id, "password": password, "submit": "Войти"}

    # 1. POST — отправка формы
    try:
        r1 = session.post(LOGIN_URL, data=payload, timeout=10)
    except Exception as e:
        print(f"❌ Ошибка POST: {e}")
        return False

    # 2. GET — получаем куки (обязательно!)
    try:
        r2 = session.get(urljoin(BASE_URL, "/student/"), timeout=10)
    except Exception as e:
        print(f"❌ Ошибка GET после входа: {e}")
        return False

    # Проверка: есть ли куки?
    php_sessid = session.cookies.get("PHPSESSID")
    if php_sessid:
        print("✅ Успешный вход (куки получены)!")
        return True

    # Резерв: проверка по тексту
    if "logout" in r2.text or "Выход" in r2.text:
        print("✅ Успешный вход (по тексту)!")
        return True

    print("❌ Вход не удался.")
    snippet = r2.text[:300].replace('\n', ' ')
    print(f"🔍 Ответ: {snippet}...")
    return False


def get_page(session, m, d):
    url = PAGE_URL.format(m=m, d=d)
    try:
        r = session.get(url, timeout=10)
    except Exception as e:
        print(f"❌ Ошибка загрузки: {e}")
        return None, url

    if r.status_code != 200:
        print(f"❌ HTTP {r.status_code}")
        return None, url

    # Проверка: не на форму ли перекинуло?
    if "регистрация" in r.text and "вход" in r.text:
        print("⚠️ Сессия устарела.")
        return None, url

    return r.text, url


def parse_table(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table.table-prog tbody tr")
    students = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) > 2:
            fio = cols[1].get_text(strip=True)
            hours = []
            for c in cols[2:]:
                nb = c.get("data-nb")
                if nb:
                    try:
                        h = json.loads(nb)
                        if h.get("userid") and h.get("zid") and h.get("hour"):
                            hours.append(h)
                    except:
                        continue
            students.append({"fio": fio, "hours": hours})
    return students


def show_students(students):
    print("\n👨‍🎓 Студенты:")
    for i, s in enumerate(students, 1):
        print(f"{i:2}. {s['fio']}")


def group_hours_by_pair(hours):
    pairs = []
    cur = None
    for h in hours:
        if not cur or cur[0]["zid"] != h["zid"]:
            cur = []
            pairs.append(cur)
        cur.append(h)
    return pairs


def parse_selection(inp, n_items):
    sel = set()
    if not inp.strip():
        return sel
    parts = inp.replace(",", " ").split()
    for p in parts:
        p = p.strip()
        if p in ("0", "all", "все"):
            sel.update(range(1, n_items + 1))
        elif "-" in p:
            try:
                a, b = map(int, p.split("-"))
                sel.update(range(a, b + 1))
            except:
                pass
        elif "." in p:
            try:
                pair, hour = map(int, p.split("."))
                if 1 <= pair <= n_items:
                    sel.add((pair, hour))
            except:
                pass
        else:
            try:
                i = int(p)
                if 1 <= i <= n_items:
                    sel.add(i)
            except:
                pass
    return sel


def get_selected_hours(pairs, sel):
    res = []
    for item in sel:
        if isinstance(item, int):
            i = item - 1
            if 0 <= i < len(pairs):
                res.extend(pairs[i])
        elif isinstance(item, tuple):
            pair_i, hour_j = item
            pair_i -= 1
            if 0 <= pair_i < len(pairs):
                pair = pairs[pair_i]
                if 1 <= hour_j <= len(pair):
                    res.append(pair[hour_j - 1])
    return res


# =============== АСИНХРОННАЯ МАССОВАЯ ОТМЕТКА ===============
async def _send_mark(session, url, h, reason, sem):
    async with sem:
        payload = {
            "userid": h["userid"],
            "zid": h["zid"],
            "hour": h["hour"],
            "nb": "on",
            "type": reason,
            "reason": ""
        }
        try:
            async with session.post(url, data=payload, timeout=10) as r:
                return r.status == 200
        except:
            return False


async def mass_mark(students_info, selection_str, reason, page_url, php_sessid):
    tasks = []
    sem = asyncio.Semaphore(10)

    for name, pairs in students_info.items():
        n = len(pairs)
        sel = parse_selection(selection_str, n)
        hours = get_selected_hours(pairs, sel)
        for h in hours:
            h["_student"] = name
            tasks.append((name, h["zid"], h["hour"], h))

    if not tasks:
        print("❌ Нечего отправлять.")
        return 0

    cookies = {"PHPSESSID": "l7igucjp76i53chu78h6rr4o7b1llr07"}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE_URL
    }

    async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
        futures = [_send_mark(session, page_url, h, reason, sem) for _, _, _, h in tasks]
        results = await asyncio.gather(*futures)

    # Группировка
    report = defaultdict(lambda: defaultdict(list))
    for (name, zid, hour, _), ok in zip(tasks, results):
        report[name][zid].append((hour, ok))

    print()
    total_ok = 0
    for name, zid_data in report.items():
        print(f"👨‍🎓 {name}:")
        for zid, hour_list in zid_data.items():
            # Номер пары — по первому студенту
            pair_num = "?"
            first_pairs = next(iter(students_info.values()))
            for i, p in enumerate(first_pairs, 1):
                if p and p[0]["zid"] == zid:
                    pair_num = i
                    break

            total = len(hour_list)
            ok_cnt = sum(ok for _, ok in hour_list)
            total_ok += ok_cnt

            if total == 1:
                h_num = hour_list[0][0]
                mark = "✅ OK" if hour_list[0][1] else "❌ ERROR"
                print(f"  Пара {pair_num} — {h_num} час {mark}")
            else:
                mark = "✅ OK" if ok_cnt == total else f"✅ {ok_cnt}/{total}"
                print(f"  Пара {pair_num} — все часы ({total}) {mark}")
        print()

    print(f"🎉 Готово! Успешно: {total_ok} из {len(tasks)}")
    return total_ok


# =============== MAIN ===============
def main():
    print("=== SFH — Student Fair Hours ===")

    creds = load_auth()
    if not creds:
        print("\n🔑 Вход впервые:")
        user_id = input("Логин (например 007): ").strip()
        password = input("Пароль: ").strip()
        creds = {"id": user_id, "password": password}
        save_auth(creds)

    session = create_session()
    if not login(session, creds):
        print("\n🔄 Повторный вход...")
        if os.path.exists(AUTH_FILE):
            os.remove(AUTH_FILE)
        user_id = input("Повторите логин: ").strip()
        password = input("Повторите пароль: ").strip()
        creds = {"id": user_id, "password": password}
        save_auth(creds)
        session = create_session()
        if not login(session, creds):
            print("🛑 Вход не удался.")
            return

    d = input("\nДень (например 6): ").strip()
    m = input("Месяц (например 11): ").strip()

    html, page_url = get_page(session, m, d)
    if not html:
        return

    students = parse_table(html)
    if not students:
        print("❌ Студенты не найдены.")
        return

    show_students(students)

    print("\nВыберите студентов (пример: 1,3,5 или 1-3 или all):")
    sel_students = input("→ ").strip().lower()
    selected = []

    if sel_students in ("all", "все", "0", ""):
        selected = students
    else:
        idxs = parse_selection(sel_students, len(students))
        for item in idxs:
            if isinstance(item, int):
                i = item - 1
                if 0 <= i < len(students):
                    selected.append(students[i])

    if not selected:
        print("❌ Никто не выбран.")
        return

    print(f"\n✅ Выбрано: {len(selected)} студент(ов)")
    for s in selected:
        print(f"  • {s['fio']}")

    pairs_by_student = {s["fio"]: group_hours_by_pair(s["hours"]) for s in selected}

    first_fio = selected[0]["fio"]
    first_pairs = pairs_by_student[first_fio]
    if first_pairs:
        print(f"\n📚 Пример пар ({first_fio}):")
        for i, p in enumerate(first_pairs, 1):
            print(f"Пара {i} (zid={p[0]['zid']})")
    else:
        print(f"\n⚠️ У {first_fio} нет пар в этот день.")

    sel_str = input("\n→ Введите пары/часы для ВСЕХ (1.1 2 3-4 или 0 для всех):\n").strip()
    print("\nТипы причин:")
    print("0 — нет | 1 — мед.справка | 2 — общественная | 3 — дежурство | 4 — объяснительная")
    reason = input("Тип причины (по умолчанию 0): ").strip() or "0"

    php_sessid = session.cookies.get("PHPSESSID")
    if not php_sessid:
        print("❌ Куки не получены. Попробуйте перезапустить.")
        return

    asyncio.run(mass_mark(pairs_by_student, sel_str, reason, page_url, php_sessid))


if __name__ == "__main__":
    main()