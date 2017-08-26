from flask import Flask, render_template, request, Response, jsonify
from pymongo import MongoClient
import os
from bson import json_util
from bson.objectid import ObjectId
import re
import json
from PIL import Image
import requests
from datetime import datetime, timedelta
from collections import OrderedDict
from dateutil import tz

app = Flask(__name__)

MONGO_URL = os.environ['MONGO_URL']

PAGE_TYPES = {
    'certificate_domicile': 'Certificate of Domicile',
    'certificate_exempting': 'Certificate Exempting from Dictation Test',
    'certificate_back': 'Back of certificate',
    'landing_form': 'Landing form',
    'landing_form_back': 'Back of Landing form',
    'other_page_type': 'Other'
}


@app.route('/pages/<barcode>/<page>/')
def get_page_details(barcode, page):
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    subj = db.subjects.find_one({'type': 'root', 'meta_data.set_key': barcode, 'location.standard': {'$regex': '{}-p{}\.jpg$'.format(barcode, page)}})
    secondary = db.subjects.find({'parent_subject_id': ObjectId(subj['_id']), 'status': 'complete'})
    subj['secondary'] = secondary
    return Response(json_util.dumps(subj), mimetype='application/json')


@app.route('/parent/<id>/')
def get_parent(id):
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    subj = db.subjects.find_one({'_id': ObjectId(id)})
    return Response(json_util.dumps(subj), mimetype='application/json')


@app.route('/subjects/<id>/')
def view_subject(id):
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    subj = db.subjects.find_one({'_id': ObjectId(id)})
    return Response(json_util.dumps(subj), mimetype='application/json')


def get_subject(id):
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    subj = db.subjects.find_one({'_id': ObjectId(id)})
    return subj


# Finished will either be transcribed + complete or consensus + complete

# root subject -> transcribed, if complete yay, if retired -> consensus if complete yay

def find_root(subj):
    while subj['type'] != 'root':
        subj = get_subject(subj['parent_subject_id'])
    return subj


@app.route('/completions/')
def find_subjects_with_data():
    pages = {}
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    completed = db.subjects.find({'status': 'complete'})
    for complete in completed:
        field = complete['type'].replace('transcribed_', '').replace('consensus_', '')
        try:
            value = complete['data']['values'][0]['value']
        except KeyError:
            value = complete['data']['value']
        root = find_root(complete)
        print root
        barcode = root['meta_data']['set_key']
        page_number = re.search(r'p(\d+)\.jpg', root['location']['standard']).group(1)
        page_id = '{}-{}'.format(barcode, page_number)
        try:
            pages[page_id]['fields'].append({'field': field, 'value': value})
        except KeyError:
            pages[page_id] = {'barcode': barcode, 'page': page_number, 'image': root['location']['standard'], 'fields': [{'field': field, 'value': value}]}
    return jsonify(pages)


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/pages/')
def get_types():
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    pipeline = [
        {'$match': {'task_key': 'pick_page_type'}},
        {'$group': {'_id': '$annotation.value', 'count': {'$sum': 1}}}
    ]
    types = db.classifications.aggregate(pipeline)
    total = 0
    type_names = []
    type_totals = []
    for type in types:
        type_names.append(PAGE_TYPES[type['_id']])
        type_totals.append(type['count'])
        total += type['count']
    return render_template('page_totals.html', total=total, type_names=json.dumps(type_names), type_totals=type_totals)


@app.route('/photos/')
def get_photos():
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    front = db.subjects.count({'type': 'marked_photo_front'})
    side = db.subjects.count({'type': 'marked_photo_side'})
    sample = list(db.subjects.find({'type': 'marked_photo_front'}).sort('updated_at', -1).limit(1))[0]
    im_url = sample['location']['standard']
    details = re.search(r'(\d+)-p(\d+)\.jpg', im_url)
    item = db.items.find_one({'identifier': details.group(1)})
    citation = 'NAA: {}, {}, p. {}'.format(item['series'], item['control_symbol'], details.group(2))
    im = Image.open(requests.get(im_url, stream=True).raw)
    coords = [
        sample['region']['x'] + 10,
        sample['region']['y'] + 10,
        sample['region']['x'] + sample['region']['width'] - 10,
        sample['region']['y'] + sample['region']['height'] - 10
    ]
    photo = im.crop(coords)
    photo.save('static/images/photo.jpg')
    return render_template('photos.html', front=front, side=side, total=front + side, citation=citation)


@app.route('/gender/')
def get_gender():
    totals = {'male': 0, 'female': 0, 'unknown': 0}
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    pipeline = [
        {'$match': {'task_key': 'pick_photo_gender'}},
        {'$group': {'_id': '$annotation.value', 'count': {'$sum': 1}}}
    ]
    results = db.classifications.aggregate(pipeline)
    for result in results:
        totals[result['_id']] = result['count']
    return render_template('gender.html', totals=totals.items())


@app.route('/classifications-week/')
def get_classifications_week():
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    yesterday = datetime.utcnow() - timedelta(days=7)
    print yesterday
    sort_dict = OrderedDict()
    sort_dict['_id.year'] = 1
    sort_dict['_id.month'] = 1
    sort_dict['_id.day'] = 1
    sort_dict['_id.hour'] = 1
    pipeline = [
        {'$match': {'created_at': {'$gt': yesterday}}},
        {'$group': {
            '_id': {
                'hour': {'$hour': '$created_at'},
                'day': {'$dayOfMonth': '$created_at'},
                'month': {'$month': '$created_at'},
                'year': {'$year': '$created_at'},
            },
            'count': {'$sum': 1}
        }},
        {'$sort': sort_dict}
    ]
    results = db.classifications.aggregate(pipeline)
    from_zone = tz.gettz('UTC')
    to_zone = tz.gettz('Australia/ACT')
    start = yesterday.replace(tzinfo=from_zone)
    start = start.astimezone(to_zone)
    start = start.astimezone(to_zone) - timedelta(hours=1)
    start = (datetime.strftime(start, '%Y-%m-%d %H:00:00'))
    end = datetime.now(to_zone)
    end = datetime.now(to_zone) + timedelta(hours=1)
    end = (datetime.strftime(end, '%Y-%m-%d %H:00:00'))
    x = []
    y = []
    total = 0
    for result in results:
        utc_date = datetime(result['_id']['year'], result['_id']['month'], result['_id']['day'], result['_id']['hour'], 0, 0)
        utc_date = utc_date.replace(tzinfo=from_zone)
        local_date = utc_date.astimezone(to_zone)
        x.append(datetime.strftime(local_date, '%Y-%m-%d %H:00:00'))
        y.append(result['count'])
        total += result['count']
    return render_template('classifications.html', x=x, y=y, start=start, end=end, title='classifications in the last week', total=total)


@app.route('/classifications-day/')
def get_classifications_day():
    dbclient = MongoClient(MONGO_URL)
    db = dbclient.get_default_database()
    yesterday = datetime.utcnow() - timedelta(days=1)
    print yesterday
    sort_dict = OrderedDict()
    sort_dict['_id.year'] = 1
    sort_dict['_id.month'] = 1
    sort_dict['_id.day'] = 1
    sort_dict['_id.hour'] = 1
    pipeline = [
        {'$match': {'created_at': {'$gt': yesterday}}},
        {'$group': {
            '_id': {
                'hour': {'$hour': '$created_at'},
                'day': {'$dayOfMonth': '$created_at'},
                'month': {'$month': '$created_at'},
                'year': {'$year': '$created_at'},
            },
            'count': {'$sum': 1}
        }},
        {'$sort': sort_dict}
    ]
    results = db.classifications.aggregate(pipeline)
    from_zone = tz.gettz('UTC')
    to_zone = tz.gettz('Australia/ACT')
    start = yesterday.replace(tzinfo=from_zone)
    start = start.astimezone(to_zone) - timedelta(hours=1)
    start = (datetime.strftime(start, '%Y-%m-%d %H:00:00'))
    end = datetime.now(to_zone) + timedelta(hours=1)
    end = (datetime.strftime(end, '%Y-%m-%d %H:00:00'))
    x = []
    y = []
    total = 0
    for result in results:
        utc_date = datetime(result['_id']['year'], result['_id']['month'], result['_id']['day'], result['_id']['hour'], 0, 0)
        utc_date = utc_date.replace(tzinfo=from_zone)
        local_date = utc_date.astimezone(to_zone)
        x.append(datetime.strftime(local_date, '%Y-%m-%d %H:00:00'))
        y.append(result['count'])
        total += result['count']
    return render_template('classifications.html', x=x, y=y, start=start, end=end, title='classifications in the last day', total=total)

