#!/usr/bin/env python3
"""Сборка сайта «Научный дайджест»: content/issues/ГГГГ-ММ-ДД.md -> _site/.

Один файл = один номер газеты. Формат — в README.md.
Зависимости: pip install markdown pyyaml jinja2
Запуск: python build.py [--content DIR] [--out DIR]
"""
import argparse
import datetime as dt
import re
import shutil
from collections import OrderedDict
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

ROOT = Path(__file__).resolve().parent

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
              "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def to_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def ru_date(value, year=True):
    d = to_date(value)
    s = f"{d.day} {MONTHS[d.month - 1]}"
    return f"{s} {d.year}" if year else s


def md(text):
    """Markdown -> HTML; внешние ссылки открываются в новой вкладке."""
    html = markdown.markdown(str(text or "").strip(), extensions=["extra", "sane_lists"])
    html = re.sub(r'<a href="(https?://)', r'<a rel="noopener" target="_blank" href="\1', html)
    return Markup(html)


def md_inline(text):
    html = str(md(text))
    html = re.sub(r"^<p>(.*)</p>$", r"\1", html, flags=re.S)
    return Markup(html)


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def fail(path, msg):
    raise SystemExit(f"{path.name}: {msg}")


def read_issue(path, site, rubrics_by_slug, statuses):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        fail(path, "нет блока front matter (--- ... ---)")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        fail(path, f"ошибка YAML: {e}")

    date = to_date(meta.get("date") or path.stem)
    first = to_date(site["first_issue"])
    number = meta.get("number") or (date - first).days + 1

    news = meta.get("news") or []
    if not news:
        fail(path, "пустой список news")
    items = []
    for i, n in enumerate(news, 1):
        for key in ("rubric", "title", "text"):
            if not n.get(key):
                fail(path, f"новость {i}: не заполнено поле «{key}»")
        if n["rubric"] not in rubrics_by_slug:
            fail(path, f"новость {i}: неизвестная рубрика «{n['rubric']}» "
                       f"(допустимо: {', '.join(rubrics_by_slug)})")
        status = n.get("status") or ""
        if status and status not in statuses:
            fail(path, f"новость {i}: неизвестный статус «{status}» (допустимо: {', '.join(statuses)})")
        doi = str(n.get("doi") or "").strip()
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
        items.append(dict(
            n,
            rubric_obj=rubrics_by_slug[n["rubric"]],
            text_html=md(n["text"]),
            title_html=md_inline(n["title"]),
            status=status,
            status_color=statuses.get(status, "#555"),
            doi=doi,
        ))

    sections = []
    counters = {}
    for r in site["rubrics"]:
        rn = [x for x in items if x["rubric"] == r["slug"]]
        for x in rn:
            counters[r["slug"]] = counters.get(r["slug"], 0) + 1
            x["anchor"] = f"{r['slug']}-{counters[r['slug']]}"
        note = (meta.get("empty") or {}).get(r["slug"]) or "За сутки значимых публикаций нет."
        sections.append(dict(rubric=r, stories=rn, empty_note=note))

    slug = date.isoformat()
    issue = dict(
        date=date,
        slug=slug,
        url=f"/issues/{slug}/",
        number=number,
        headline=[md_inline(h) for h in (meta.get("headline") or [])],
        headline_text=[re.sub(r"<[^>]+>", "", str(md_inline(h))) for h in (meta.get("headline") or [])],
        sections=sections,
        items=items,
        count=len(items),
        figure=meta.get("figure") or None,
        further=meta.get("further") or [],
        editorial=md(m.group(2)) if m.group(2).strip() else "",
        draft=bool(meta.get("draft", False)),
    )
    if issue["figure"]:
        issue["figure"]["text_html"] = md_inline(issue["figure"].get("text", ""))
    for x in items:
        x["issue"] = issue
    return issue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", default=str(ROOT / "content" / "issues"))
    ap.add_argument("--out", default=str(ROOT / "_site"))
    args = ap.parse_args()

    site = yaml.safe_load((ROOT / "site.yml").read_text(encoding="utf-8"))
    site["url"] = site["url"].rstrip("/")
    rubrics = site["rubrics"]
    rubrics_by_slug = {r["slug"]: r for r in rubrics}
    statuses = {s["name"]: s["color"] for s in site["statuses"]}

    issues = [read_issue(p, site, rubrics_by_slug, statuses)
              for p in sorted(Path(args.content).glob("*.md"))]
    issues = [i for i in issues if not i["draft"]]
    issues.sort(key=lambda i: i["date"], reverse=True)
    for k, i in enumerate(issues):
        i["newer"] = issues[k - 1] if k > 0 else None
        i["older"] = issues[k + 1] if k + 1 < len(issues) else None

    for r in rubrics:
        r["stories"] = [x for i in issues for x in i["items"] if x["rubric"] == r["slug"]]

    by_month = OrderedDict()
    for i in issues:
        by_month.setdefault((i["date"].year, i["date"].month), []).append(i)
    months = [(f"{MONTHS_NOM[m - 1]} {y}", lst) for (y, m), lst in by_month.items()]

    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                      autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["ru_date"] = ru_date
    env.filters["weekday"] = lambda d: WEEKDAYS[to_date(d).weekday()]
    env.filters["rfc822"] = lambda d: dt.datetime.combine(to_date(d), dt.time(6, 0)).strftime(
        "%a, %d %b %Y %H:%M:%S +0300")
    env.filters["plural"] = plural
    env.globals.update(site=site, rubrics=rubrics, year=dt.date.today().year,
                       latest=issues[0] if issues else None)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    def render(template, dest, **ctx):
        path = out / dest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(**ctx), encoding="utf-8")

    if issues:
        render("issue.html", "index.html", issue=issues[0], recent=issues[1:8],
               page_url="/", is_home=True)
    else:
        render("empty.html", "index.html", page_url="/")
    for i in issues:
        render("issue.html", f"issues/{i['slug']}/index.html", issue=i, page_url=i["url"])
    render("archive.html", "archive/index.html", months=months, page_url="/archive/")
    for r in rubrics:
        render("rubric.html", f"rubric/{r['slug']}/index.html", rubric=r,
               items=r["stories"][:80], page_url=f"/rubric/{r['slug']}/")
    render("about.html", "about/index.html", page_url="/about/")
    render("privacy.html", "privacy/index.html", page_url="/privacy/")
    render("404.html", "404.html", page_url="/404.html")
    render("feed.xml", "feed.xml", issues=issues[:30])
    render("sitemap.xml", "sitemap.xml", issues=issues)
    render("robots.txt", "robots.txt")

    shutil.copytree(ROOT / "static", out, dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")
    if site.get("domain"):
        (out / "CNAME").write_text(site["domain"].strip() + "\n")
    total = sum(i["count"] for i in issues)
    print(f"Собрано: {len(issues)} номеров, {total} материалов -> {out}")


if __name__ == "__main__":
    main()
