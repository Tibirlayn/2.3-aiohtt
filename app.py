from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from aiohttp import web


DATABASE = web.AppKey('database', Path)
routes = web.RouteTableDef()


async def init_db(app):
    async with aiosqlite.connect(app[DATABASE]) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL,
                owner TEXT NOT NULL
            )
        ''')
        await db.commit()


@web.middleware
async def handle_errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as error:
        headers = {}
        if 'Allow' in error.headers:
            headers['Allow'] = error.headers['Allow']
        return web.json_response(
            {'error': error.reason}, status=error.status, headers=headers,
        )


@web.middleware
async def database_connection(request, handler):
    async with aiosqlite.connect(request.app[DATABASE]) as db:
        db.row_factory = aiosqlite.Row
        request['db'] = db
        return await handler(request)


async def get_json(request):
    if request.content_type != 'application/json':
        raise web.HTTPUnsupportedMediaType(reason='Нужен Content-Type: application/json')
    try:
        return await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(reason='Некорректный JSON')


def validate_ad(data, partial=False):
    if not isinstance(data, dict) or not data:
        return 'Нужно передать JSON-объект с полями объявления'

    fields = {'title', 'description', 'owner'}
    if set(data) - fields:
        return 'Допустимые поля: title, description, owner'
    if not partial and fields - set(data):
        return 'Обязательные поля: title, description, owner'

    for field, value in data.items():
        if not isinstance(value, str) or not value.strip():
            return f'Поле {field} должно быть непустой строкой'
    return None


@routes.post('/ads')
async def create_ad(request):
    data = await get_json(request)
    error = validate_ad(data)
    if error:
        return web.json_response({'error': error}, status=400)

    db = request['db']
    created_at = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        'INSERT INTO ads (title, description, created_at, owner) VALUES (?, ?, ?, ?)',
        (data['title'], data['description'], created_at, data['owner']),
    )
    ad_id = cursor.lastrowid
    await cursor.close()
    await db.commit()
    async with db.execute('SELECT * FROM ads WHERE id = ?', (ad_id,)) as cursor:
        ad = await cursor.fetchone()
    return web.json_response(dict(ad), status=201)


@routes.get(r'/ads/{ad_id:\d+}')
@routes.patch(r'/ads/{ad_id:\d+}')
@routes.delete(r'/ads/{ad_id:\d+}')
async def ad_detail(request):
    ad_id = int(request.match_info['ad_id'])
    db = request['db']
    async with db.execute('SELECT * FROM ads WHERE id = ?', (ad_id,)) as cursor:
        ad = await cursor.fetchone()
    if ad is None:
        return web.json_response({'error': 'Объявление не найдено'}, status=404)

    if request.method in ('GET', 'HEAD'):
        return web.json_response(dict(ad))

    if request.method == 'DELETE':
        await db.execute('DELETE FROM ads WHERE id = ?', (ad_id,))
        await db.commit()
        return web.Response(status=204)

    data = await get_json(request)
    error = validate_ad(data, partial=True)
    if error:
        return web.json_response({'error': error}, status=400)

    updated = dict(ad)
    updated.update(data)
    await db.execute(
        'UPDATE ads SET title = ?, description = ?, owner = ? WHERE id = ?',
        (updated['title'], updated['description'], updated['owner'], ad_id),
    )
    await db.commit()
    return web.json_response(updated)


app = web.Application(middlewares=[handle_errors, database_connection])
app[DATABASE] = Path(__file__).with_name('ads.db')
app.on_startup.append(init_db)
app.add_routes(routes)


if __name__ == '__main__':
    web.run_app(app, host='127.0.0.1', port=5000)
