import requests
from bs4 import BeautifulSoup
import json
import os
from urllib.parse import urljoin

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
    
    # Извлекаем логин (поддержка "id" и "login")
    user_id = creds.get("id") or creds.get("login") or ""
    password = creds.get("password") or creds.get("pass") or ""

    # ✅ Правильные поля формы:
    payload = {
        "id": user_id,          # ← КЛЮЧЕВОЕ: именно "id"
        "password": password,
        "submit": "Войти"
    }

    try:
        r = session.post(LOGIN_URL, data=payload, timeout=10)
    except Exception as e:
        print(f"❌ Ошибка соединения: {e}")
        return False

    print(f"→ POST {LOGIN_URL}")
    print(f"← Статус: {r.status_code}")
    print(f"🍪 Cookies после входа: {dict(session.cookies)}")

    # Успешный вход: есть PHPSESSID
    if session.cookies.get("PHPSESSID"):
        print("✅ Успешный вход!")
        return True

    # Или: в теле нет "регистрация" и "вход"
    text = r.text.strip()
    if "регистрация" in text and "вход" in text:
        print("❌ Сервер вернул форму входа — проверьте логин/пароль.")
        return False

    # Или: редирект на главную/расписание
    if "logout" in text or "Выход" in text:
        print("✅ Успешный вход (по тексту)!")
        return True

    print("❌ Неизвестная ошибка. Ответ:")
    print(text[:500].replace('\n', ' '))
    return False


def get_page(session, m, d):
    url = PAGE_URL.format(m=m, d=d)
    print(f"\n📅 Загружаем: {url}")
    try:
        r = session.get(url, timeout=10)
    except Exception as e:
        print(f"❌ Ошибка загрузки: {e}")
        return None, url

    if r.status_code != 200:
        print(f"❌ HTTP {r.status_code}")
        return None, url

    text = r.text.strip()
    if "регистрация" in text and "вход" in text:
        print("⚠️ Сессия устарела — требуется повторный вход.")
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


def parse_selection(inp, n_pairs):
    sel = set()
    if not inp.strip(): return sel
    parts = inp.replace(",", " ").split()
    for p in parts:
        p = p.strip()
        if p == "0":
            sel.update(range(1, n_pairs + 1))
        elif "-" in p:
            try:
                a, b = map(int, p.split("-"))
                sel.update(range(a, b + 1))
            except: pass
        elif "." in p:
            try:
                pair, hour = map(int, p.split("."))
                if 1 <= pair <= n_pairs:
                    sel.add((pair, hour))
            except: pass
        else:
            try:
                i = int(p)
                if 1 <= i <= n_pairs:
                    sel.add(i)
            except: pass
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
        print("\n🔄 Повторный вход (очищаем старые данные)...")
        if os.path.exists(AUTH_FILE):
            os.remove(AUTH_FILE)
            print(f"🗑️ Удалён старый {AUTH_FILE}")
        # Повторный ввод
        user_id = input("Повторите логин: ").strip()
        password = input("Повторите пароль: ").strip()
        creds = {"id": user_id, "password": password}
        save_auth(creds)
        session = create_session()
        if not login(session, creds):
            print("🛑 Вход не удался. Проверьте данные вручную на сайте.")
            return

    d = input("\nДень (например 6): ").strip()
    m = input("Месяц (например 11): ").strip()

    html, page_url = get_page(session, m, d)
    if not html:
        print("🛑 Страница не загружена.")
        return

    students = parse_table(html)
    if not students:
        print("❌ Студенты не найдены.")
        return

    show_students(students)

    try:
        idx = int(input("\nВыберите студента №: ")) - 1
        student = students[idx]
    except (ValueError, IndexError):
        print("❌ Неверный номер.")
        return

    print(f"\n✅ Выбран: {student['fio']}")
    pairs = group_hours_by_pair(student["hours"])
    if not pairs:
        print("❌ Пар нет.")
        return

    print("\n📚 Пары:")
    for i, p in enumerate(pairs, 1):
        print(f"Пара {i} (zid={p[0]['zid']})")

    sel_str = input("\n→ Выбор (1, 1.2, 1-3, 0 и т.д.): ").strip()
    sel = parse_selection(sel_str, len(pairs))
    selected = get_selected_hours(pairs, sel)
    if not selected:
        print("❌ Ничего не выбрано.")
        return

    print(f"\n🎯 Отмечаем {len(selected)} часов.")

    print("\nТипы причин:")
    print("0 — нет | 1 — мед.справка | 2 — общественная | 3 — дежурство | 4 — объяснительная")
    reason = input("Тип причины (по умолчанию 0): ").strip() or "0"

    # ✅ Отправка
    session.headers["Referer"] = page_url
    session.headers["X-Requested-With"] = "XMLHttpRequest"

    print(f"\n📤 Отправка на: {page_url}")
    success = 0
    for h in selected:
        payload = {
            "userid": h["userid"],
            "zid": h["zid"],
            "hour": h["hour"],
            "nb": "on",       # ← "on", а не "1"
            "type": reason,
            "reason": ""
        }

        try:
            r = session.post(page_url, data=payload, timeout=10)
            if r.status_code == 200:
                print(f"✅ Час {h['hour']} (zid={h['zid']}) — OK")
                success += 1
            else:
                print(f"❌ Час {h['hour']} — {r.status_code}")
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")

    print(f"\n🎉 Готово! Успешно: {success} из {len(selected)}")


if __name__ == "__main__":
    main()