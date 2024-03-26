#! /usr/bin/env python3
# -*- coding: utf-8 -*-

'''
COMP9321 24T1 Assignment 2
Data publication as a RESTful service API

Getting Started
---------------

1. You MUST rename this file according to your zID, e.g., z1234567.py.

2. To ensure your submission can be marked correctly, you're strongly encouraged
   to create a new virtual environment for this assignment.  Please see the
   instructions in the assignment 1 specification to create and activate a
   virtual environment.

3. Once you have activated your virtual environment, you need to install the
   following, required packages:

   pip install python-dotenv==1.0.1
   pip install google-generativeai==0.4.1

   You may also use any of the packages we've used in the weekly labs.
   The most likely ones you'll want to install are:

   pip install flask==3.0.2
   pip install flask_restx==1.3.0
   pip install requests==2.31.0

4. Create a file called `.env` in the same directory as this file.  This file
   will contain the Google API key you generatea in the next step.

5. Go to the following page, click on the link to "Get an API key", and follow
   the instructions to generate an API key:

   https://ai.google.dev/tutorials/python_quickstart

6. Add the following line to your `.env` file, replacing `your-api-key` with
   the API key you generated, and save the file:

   GOOGLE_API_KEY=your-api-key

7. You can now start implementing your solution. You are free to edit this file how you like, but keep it readable
   such that a marker can read and understand your code if necessary for partial marks.

Submission
----------

You need to submit this Python file and a `requirements.txt` file.

The `requirements.txt` file should list all the Python packages your code relies
on, and their versions.  You can generate this file by running the following
command while your virtual environment is active:

pip freeze > requirements.txt

You can submit the two files using the following command when connected to CSE,
and assuming the files are in the current directory (remember to replace `zid`
with your actual zID, i.e. the name of this file after renaming it):

give cs9321 assign2 zid.py requirements.txt

You can also submit through WebCMS3, using the tab at the top of the assignment
page.

'''

# You can import more modules from the standard library here if you need them
# (which you will, e.g. sqlite3).
import os
from pathlib import Path
import sqlite3
import requests
from flask import Flask, request, jsonify, url_for, abort
from flask_restx import Api, Resource, fields
from datetime import datetime

# You can import more third-party packages here if you need them, provided
# that they've been used in the weekly labs, or specified in this assignment,
# and their versions match.
from dotenv import load_dotenv  # Needed to load the environment variables from the .env file
import google.generativeai as genai  # Needed to access the Generative AI API

studentid = Path(__file__).stem  # Will capture your zID from the filename.
db_file = f"{studentid}.db"  # Use this variable when referencing the SQLite database file.
txt_file = f"{studentid}.txt"  # Use this variable when referencing the txt file for Q7.
app = Flask(__name__)
api = Api(app, doc='/swagger.json')
app.config['HOST_NAME'] = '127.0.0.1'
app.config['PORT'] = 5000  # Adjust as needed

# Load the environment variables from the .env file
load_dotenv()

# Configure the API key
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

# Create a Gemini Pro model
gemini = genai.GenerativeModel('gemini-pro')

'''
if __name__ == "__main__":
    # Here's a quick example of using the Generative AI API:
    question = "Give me some facts about UNSW!"
    response = gemini.generate_content(question)
    print(question)
    print(response.text)
'''


def get_db_connection():
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row  # This enables column access by name: row['column_name']
    return conn


def insert_dict_into_table(conn, table_name, data_dict):
    # Construct column names and placeholders for values
    columns = ', '.join(data_dict.keys())
    placeholders = ':'+', :'.join(data_dict.keys())  # Named placeholders for each key in the dictionary

    # Check if stop already exists (using stop_id for uniqueness)
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {table_name} WHERE stop_id=?", (data_dict['stop_id'],))
    existing_stop = cursor.fetchone()

    if existing_stop:
        # Update existing stop's last_updated timestamp
        sql = f"UPDATE {table_name} SET last_updated=? WHERE stop_id=?"
        cursor.execute(sql, (data_dict['last_updated'], data_dict['stop_id']))
        conn.commit()
        print(f"Stop {data_dict['stop_id']} updated")
    else:
        # Insert new stop if it doesn't exist
        sql = f'INSERT INTO {table_name} ({columns}) VALUES ({placeholders})'
        cursor.execute(sql, data_dict)
        conn.commit()
        print(f"Stop {data_dict['stop_id']} imported")


def init_db():
    with app.app_context():
        db = get_db_connection()
        cursor = db.cursor()
        # Create stops table if it doesn't exist
        cursor.execute(""" CREATE TABLE IF NOT EXISTS stops (
                            stop_id INTEGER PRIMARY KEY,
                            last_updated TEXT NOT NULL,
                            name TEXT NOT NULL,
                            latitude REAL NOT NULL,
                            longitude REAL NOT NULL,
                            next_departure TEXT NULL
                        ); """)
        db.commit()
        db.close()

def get_nearby_stop(stop_id):
    db = get_db_connection()
    cursor = db.cursor()
    # Query for the next stop
    cursor.execute('SELECT stop_id FROM stops WHERE stop_id > ? ORDER BY stop_id LIMIT 1', (stop_id,))
    next_stop = cursor.fetchone()
    next_stop_id = next_stop[0] if next_stop else None

    # Query for the previous stop
    cursor.execute('SELECT stop_id FROM stops WHERE stop_id < ? ORDER BY stop_id DESC LIMIT 1', (stop_id,))
    prev_stop = cursor.fetchone()
    prev_stop_id = prev_stop[0] if prev_stop else None

    db.close()

    return {
        "output_next": next_stop_id,
        "output_prev": prev_stop_id
    }

@api.route('/stops')
class Stops(Resource):
    @api.expect(api.model('StopQuery', {'query': fields.String(required=True)}))
    @api.response(201, 'Stops retrieved successfully')
    @api.response(404, 'No stops found matching the query')
    @api.response(503, 'Deutsche Bahn API unavailable')
    def put(self):
        query = request.json.get('query')
        if not query:
            return jsonify({'error': 'Invalid query string'}), 400

        try:
            # Get stops from Deutsche Bahn API
            response = requests.get('https://v6.db.transport.rest/locations', params={'query': query, 'limit': 5})
            print("RESPONSE = ")
            print(response)
            response.raise_for_status()
            stops_data = response.json()
            print("=========================================")
            print("STOPS DATA = ")
            print(stops_data)
            last_updated = datetime.now().strftime("%Y-%m-%d-%H:%M:%S")

            stops_data.sort(key=lambda stop: stop["id"])

            for stop in stops_data:
                data = {
                    "stop_id": stop["id"],
                    "last_updated": last_updated,
                    "name":  stop["name"],
                    "latitude": stop["location"]["latitude"],
                    "longitude": stop["location"]["longitude"],
                    # TODO: for each stop need to call /departure API and get the top of the # list
                    "next_departure": None
                }
                db = get_db_connection()
                insert_dict_into_table(db, "stops", data)

            stops = (  # Use parentheses for generator expression
                {
                    'stop_id': stop['id'],
                    'last_updated': last_updated,
                    '_links': {
                        'self': {
                            'href': f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{stop['id']}"
                        }
                    }
                }
                for stop in stops_data
            )

            stops = list(stops)
            print("=========================================")
            print("STOPS = ")
            print(stops)

            json_response = jsonify(stops)
            print("=========================================")
            print("JSON RESPONSE = ")
            print(json_response)
            print(app.url_map)

            return stops, 201  # Created

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return jsonify({'error': 'No stops found'}), 404
            else:
                return jsonify({'error': 'Deutsche Bahn API unavailable'}), 503
        except requests.exceptions.RequestException as e:
            return jsonify({'error': 'An error occurred'}), 500

def get_next_departure_from_api(stop_id):
  # Call external API to retrieve departures
  dep_response = requests.get(f'https://v6.db.transport.rest/stops/{stop_id}/departures', params={'duration': 120})
  dep_response.raise_for_status()
  departures = dep_response.json()

  # Find the first departure with valid platform and direction
  for departure in departures["departures"]:
      if "platform" in departure and departure["platform"] is not None and "direction" in departure and departure["direction"] is not None:
          try:
              #station_name = departure["stop"]["name"]
              station_name = "Platform "+departure["platform"]+" towards "+departure["direction"]
              return station_name  # Return station name if found
          except KeyError:
              print("Error: Station information unavailable for the first departure with valid platform and direction.")
              break

  # No valid departure found, return None
  return None

@api.route('/stops/<int:stop_id>')
class Stop(Resource):
    def get(self, stop_id):
        conn = get_db_connection()
        cursor = conn.cursor()

        # Define the query to retrieve stop details
        cursor.execute("SELECT * FROM stops WHERE stop_id = ?", (stop_id,))
        stop_data = cursor.fetchone()

        if stop_data is None:
            # Stop not found, return 404 error
            abort(404, "Stop not found")

        # Convert row object to dictionary for easier access
        stop_dict = dict(stop_data)

        next_departure = get_next_departure_from_api(stop_id)
        if next_departure is not None:
            stop_dict["next_departure"] = next_departure
        else:
            stop_dict["next_departure"] = None

        # Update the database with the retrieved next departure time (if available)
        if "next_departure" in stop_dict:
            cursor.execute("UPDATE stops SET next_departure = ? WHERE stop_id = ?", (stop_dict["next_departure"], stop_id))
            conn.commit()
        
        nearby_stop = get_nearby_stop(stop_id)

        links = {
            "self": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{stop_id}",
            "next": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{nearby_stop["output_next"]}",
            "prev": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{nearby_stop["output_prev"]}",
        }

        stop_dict = {**stop_dict, "_links": links}

        conn.close()

        return stop_dict, 200

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
