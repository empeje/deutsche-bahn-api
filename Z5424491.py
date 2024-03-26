import os
from pathlib import Path
import sqlite3
import requests
from flask import Flask, json, request, jsonify, url_for, abort
from flask_restx import Api, Resource, fields
from datetime import datetime

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

def validate_parameters(parameters):
    allowed_parameters = {'last_updated', 'name', 'latitude', 'longitude', 'next_departure'}
    forbidden_parameters = {'stop_id', '_links'}
    valid_parameters = set()
    invalid_parameters = set()

    for param in parameters:
        if param in allowed_parameters:
            valid_parameters.add(param)
        elif param in forbidden_parameters:
            print("------------")
            print("forbidden")
            return None, f"Parameter '{param}' is not permitted"
        else:
            invalid_parameters.add(param)

    print("------------")
    print(valid_parameters)

    if invalid_parameters:
        return None, f"Invalid parameters: {', '.join(invalid_parameters)}"

    return valid_parameters, None

# Function to update _links for the whole database
def update_links():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT stop_id FROM stops")
    stop_ids = [row['stop_id'] for row in cursor.fetchall()]

    # Update _links for each stop in the database
    for stop_id in stop_ids:
        nearby_stop = get_nearby_stop(stop_id)
        links = {
            "self": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{stop_id}",
            "next": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{nearby_stop['output_next']}",
            "prev": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{nearby_stop['output_prev']}",
        }
        cursor.execute("UPDATE stops SET _links = ? WHERE stop_id = ?", (json.dumps(links), stop_id))
        conn.commit()

    conn.close()

@api.route('/stops/<int:stop_id>')
@api.param('stop_id', 'The ID of the stop to retrieve information about')
class Stop(Resource):
    def get(self, stop_id):
        conn = get_db_connection()
        cursor = conn.cursor()

        params = request.args.to_dict(flat=True)
        include_fields=params.get("include").split(',') if params.get("include") else None

        if include_fields:
            valid_params, error_message = validate_parameters(include_fields)
            print(valid_params)

            if valid_params is None:
                print("no valid params")
                return {'message': error_message}, 400        
        
        # Define the query to retrieve stop details
        cursor.execute(f"SELECT {", ".join(valid_params) if include_fields else "*"} FROM stops WHERE stop_id = ?", (stop_id,))
        stop_data = cursor.fetchone()

        if stop_data is None:
            # Stop not found, return 404 error
            abort(404, "Stop not found")

        # Convert row object to dictionary for easier access
        stop_dict = dict(stop_data)

        next_departure = get_next_departure_from_api(stop_id)
        if not include_fields or (include_fields and "next_departure" in valid_params):
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
            "next": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{nearby_stop["output_next"]}" if nearby_stop["output_next"] else None,
            "prev": f"http://{app.config['HOST_NAME']}:{app.config['PORT']}/stops/{nearby_stop["output_prev"]}" if nearby_stop["output_prev"] else None,
        }

        stop_dict = {**stop_dict, "_links": links}

        conn.close()

        return stop_dict, 200
    
    
    
    def delete(self, stop_id):
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if the stop exists in the database
        cursor.execute("SELECT * FROM stops WHERE stop_id = ?", (stop_id,))
        stop_data = cursor.fetchone()

        if stop_data is None:
            # Stop not found, return 404 error
            conn.close()
            return {'message': f"The stop_id {stop_id} was not found in the database."}, 404

        # Delete the stop from the database
        cursor.execute("DELETE FROM stops WHERE stop_id = ?", (stop_id,))
        conn.commit()
        conn.close()

        # Update _links for the whole database
        #update_links()

        return {'message': f"The stop_id {stop_id} was removed from the database.", 'stop_id': stop_id}, 200

    

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
