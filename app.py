from flask import Flask, request, jsonify
from pymongo import MongoClient
import pandas as pd
from surprise import Dataset, Reader, SVD
from surprise.model_selection import train_test_split
from surprise.dump import dump, load
from flask_cors import CORS, cross_origin
import json
import os
from dotenv import load_dotenv
import math
import time

script_dir = os.path.dirname(os.path.abspath(__file__))

root_dir = os.path.abspath(os.path.join(script_dir, '..'))

env_file_path = os.path.join(root_dir, '.env')

load_dotenv(env_file_path)

MONGO_URL = os.environ.get('MONGO_URL')


app = Flask(__name__)
CORS(app)


client = MongoClient(MONGO_URL)
db = client['blogminds']
blogs_collection = db['blogs']
users_collection = db['users']

unrated_items = []
last_updated = 0

def get_unrated_items():
    global unrated_items
    global last_updated
    current_time = time.time()
    if current_time - last_updated > 60:
        unrated_items = list(blogs_collection.distinct('_id'))
        last_updated = current_time
    return unrated_items

def get_blogs(user_id, page=1, page_size=10):
    model_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'model.dump')
    loaded_model_tuple = load(model_path)
    loaded_model = loaded_model_tuple[1]

    unrated_items = get_unrated_items()
    
    predictions = [(item_id, loaded_model.predict(user_id, item_id).est) for item_id in unrated_items]
    
    sorted_predictions = sorted(predictions, key=lambda x: x[1], reverse=True)
    
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    paginated_predictions = sorted_predictions[start_index:end_index]

    top_blog_ids = [item_id for item_id, _ in paginated_predictions]
    # Fetch details of the top recommended blogs
    pipeline = [
        {"$match": {'_id': {'$in': top_blog_ids}}},
        {"$lookup": {
            "from": "users",
            "localField": "author",
            "foreignField": "_id",
            "as": "author_info"
        }},
        {"$unwind": "$author_info"},
        {"$project": {
            "title": 1,
            "description": 1,
            "img": 1,
            "tags": 1,
            "views": 1,
            "likesCount": 1,
            "commentsCount": 1,
            "createdAt": 1,
            "updatedAt": 1,
            "author": {
                "_id": "$author_info._id",
                "name": "$author_info.name",
                "profileImage": "$author_info.profileImage"
            }
        }}
    ]

    top_blogs = list(blogs_collection.aggregate(pipeline))

    for blog in top_blogs:
        blog['_id'] = str(blog['_id'])
        blog['author']['_id'] = str(blog['author']['_id'])

    response = {'user_id': str(user_id), 'top_recommendations': top_blogs}
    
    return jsonify(response)



def train_model():
    user_item_rating_data = []

    for user in users_collection.find():
        user_id = str(user['_id'])
        user_interests = user.get('myInterests', [])
        following = user.get('following', [])
        articles_read = user.get('readArticles', [])
        articles_wrote = user.get('blogs', [])
        for blog in blogs_collection.find():
            item_id = blog['_id']
            rating = 0
            if item_id in articles_read:
                rating += 0.5  # Increment rating if the user has read the article
            if item_id in articles_wrote:
                rating += 0.5  # Increment rating if the user has written the article
            if blog.get('tags') and any(tag in user_interests for tag in blog['tags']):
                rating += 0.5  # Increment rating if the article matches user interests
            if blog.get('author') in following:
                rating += 0.5  # Increment rating if the article is written by a user the current user is following
       
            rating += math.log(1 + blog.get('views', 1)) * 0.2
            rating += math.log(1 + blog.get('likesCount', 1)) * 0.2
            user_item_rating_data.append({'user_id': user_id, 'item_id': item_id, 'rating': rating})


    reader = Reader(rating_scale=(0, 10))
    data = Dataset.load_from_df(pd.DataFrame(user_item_rating_data), reader)
    trainset, _ = train_test_split(data, test_size=0.2)
    model = SVD()
    model.fit(trainset)
    model_dump_file = 'model.dump'
    dump(model_dump_file, algo=model)
    return jsonify({'message': 'Model trained successfully'})

app = Flask(__name__)

@app.route('/get_blogs', methods=['GET'])
@cross_origin()
def get_blog_route():
    user_id = request.args.get('user_id')
    page = request.args.get('page')
    page_size = request.args.get('page_size')
    if not user_id:
        return jsonify({'error': 'User ID parameter is missing'}), 400
    return get_blogs(user_id, int(page), int(page_size))


@app.route('/train_model', methods=['get'])
def train_model_route():
    return train_model()

if __name__ == '__main__':
    app.run(debug=True)
