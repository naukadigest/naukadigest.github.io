#!/usr/bin/env python3
"""Сборка сайта: несколько газет, content/<газета>/ГГГГ-ММ-ДД.md -> _site/<газета>/.

Один файл = один номер газеты. Формат — в README.md.
Зависимости: pip install markdown pyyaml jinja2
Запуск: python build.py [--content DIR] [--out DIR]
"""
import argparse
import datetime as dt
import html as html_lib
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
        headline_text=[html_lib.unescape(re.sub(r"<[^>]+>", "", str(md_inline(h)))) for h in (meta.get("headline") or [])],
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


def build_edition(site, ed, content_root, out, env, statuses):
    """Одна газета: content/<slug>/*.md -> _site/<slug>/."""
    cfg = dict(site, **{k: v for k, v in ed.items() if k != "rubrics"})
    rubrics = [dict(r) for r in ed["rubrics"]]
    cfg["rubrics"] = rubrics
    rubrics_by_slug = {r["slug"]: r for r in rubrics}
    base = "/" + ed["slug"]

    src = content_root / ed["slug"]
    issues = [read_issue(p, cfg, rubrics_by_slug, statuses) for p in sorted(src.glob("*.md"))]
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

    common = dict(site=cfg, rubrics=rubrics, base=base, latest=issues[0] if issues else None)
    dest_root = out / ed["slug"]

    def render(template, dest, **ctx):
        path = dest_root / dest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(**common, **ctx), encoding="utf-8")

    # Главная газеты — список всех номеров, свежий сверху, далее по убыванию
    render("edition.html", "index.html", months=months, count=len(issues), page_url="/")
    for i in issues:
        render("issue.html", f"issues/{i['slug']}/index.html", issue=i, page_url=i["url"])
    # старый адрес архива ведёт на главную газеты
    render("redirect.html", "archive/index.html", target=base + "/", page_url="/archive/")
    for r in rubrics:
        render("rubric.html", f"rubric/{r['slug']}/index.html", rubric=r,
               items=r["stories"][:80], page_url=f"/rubric/{r['slug']}/")
    render("about.html", "about/index.html", page_url="/about/")
    render("privacy.html", "privacy/index.html", page_url="/privacy/")
    render("sitemap.xml", "sitemap.xml", issues=issues)
    total = sum(i["count"] for i in issues)
    print(f"{base}/: {len(issues)} номеров, {total} материалов")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", default=str(ROOT / "content"))
    ap.add_argument("--out", default=str(ROOT / "_site"))
    args = ap.parse_args()

    site = yaml.safe_load((ROOT / "site.yml").read_text(encoding="utf-8"))
    site["url"] = site["url"].rstrip("/")
    statuses = {s["name"]: s["color"] for s in site["statuses"]}

    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                      autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["ru_date"] = ru_date
    env.filters["weekday"] = lambda d: WEEKDAYS[to_date(d).weekday()]
    env.filters["rfc822"] = lambda d: dt.datetime.combine(to_date(d), dt.time(6, 0)).strftime(
        "%a, %d %b %Y %H:%M:%S +0300")
    env.filters["plural"] = plural
    env.filters["md"] = md
    env.globals.update(year=dt.date.today().year)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for ed in site["editions"]:
        build_edition(site, ed, Path(args.content), out, env, statuses)

    # Корень сайта: пустая страница, общая 404 и robots.txt
    for name in ("index.html", "404.html", "robots.txt"):
        (out / name).write_text(env.get_template("root/" + name).render(
            site=site, editions=site["editions"]), encoding="utf-8")

    shutil.copytree(ROOT / "static", out, dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")
    if site.get("domain"):
        (out / "CNAME").write_text(site["domain"].strip() + "\n")
    print(f"Готово -> {out}")


if __name__ == "__main__":
    main()
