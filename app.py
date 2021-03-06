import json
from typing import List
from urllib.parse import urlencode

import os
import requests

from oic import rndstr
from oic.oauth2 import AuthorizationResponse
from oic.oic import Client
from oic.oic.message import OpenIDSchema, AccessTokenResponse
from oic.utils.authn.client import ClientSecretBasic, ClientSecretPost

from flask import *
from dotenv import load_dotenv
from pathlib import Path
from utils import clear_user_session


load_dotenv()
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

AUTHMACHINE_URL = os.getenv("AUTHMACHINE_URL")
AUTHMACHINE_CLIENT_ID = os.getenv("AUTHMACHINE_CLIENT_ID")
AUTHMACHINE_CLIENT_SECRET = os.getenv("AUTHMACHINE_CLIENT_SECRET")
AUTHMACHINE_SCOPE = os.getenv("AUTHMACHINE_SCOPE")
AUTHMACHINE_API_TOKEN = os.getenv("AUTHMACHINE_API_TOKEN")
FLASK_DEBUG = os.getenv("FLASK_DEBUG")


class AuthMachineClient(object):
    def __init__(self):
        self.client = self.get_client()
        if request.is_secure:
            proto = 'https://'
        else:
            proto = 'http://'
        self.host = proto + request.host

    def get_client(self):
        client = Client(client_authn_method={
            'client_secret_post': ClientSecretPost,
            'client_secret_basic': ClientSecretBasic
        })
        client.provider_config(AUTHMACHINE_URL)
        client.client_id = AUTHMACHINE_CLIENT_ID
        client.client_secret = AUTHMACHINE_CLIENT_SECRET
        client.verify_ssl = True
        return client

    def get_authorization_url(self):
        nonce = rndstr()

        args = {
            'client_id': self.client.client_id,
            'response_type': 'code',
            'scope': AUTHMACHINE_SCOPE,
            'claims': json.dumps({
                'authmachine_permissions': ['object1', 'object2'],
            }),
            'nonce': nonce,
            'redirect_uri': self.host + url_for('auth_callback'),
            'state': 'some-state-which-will-be-returned-unmodified'
        }
        url = self.client.provider_info['authorization_endpoint'] + '?' + urlencode(args, True)
        return url

    def get_logout_url(self):
        args = {
            'scope': AUTHMACHINE_SCOPE,
            'post_logout_redirect_uri': self.host + url_for('auth_logout_callback'),
            'state': 'some-state-which-will-be-returned-unmodified',
            'revoke_tokens': 1
        }
        url = self.client.provider_info['end_session_endpoint'] + '?' + urlencode(args, True)
        return url

    def get_access_token(self, a_resp):
        """Gets access token from AuthMachine.
        Args:
            a_resp (AuthorizationResponse):
        """
        args = {
            'code': a_resp['code'],
            'client_id': self.client.client_id,
            'client_secret': self.client.client_secret,
            'redirect_uri': self.host + url_for('auth_callback')
        }

        return self.client.do_access_token_request(
            scope=AUTHMACHINE_SCOPE,
            state=a_resp['state'],
            request_args=args,
            authn_method='client_secret_post')

    def get_userinfo(self, authorization_response):
        """Returns Open ID userinfo as dict.
        """

        user_info = self.client.do_user_info_request(
            state=authorization_response['state'],
            authn_method='client_secret_post')
        return user_info.to_dict()

    def get_authorization_response(self):
        authorization_response = self.client.parse_response(
            AuthorizationResponse,
            info=request.args,
            sformat='dict')
        return authorization_response

    def do_api_request(self, method, url, payload=None, query_params=None, **kwargs):
        assert AUTHMACHINE_API_TOKEN is not None, "Can't perform an API Request: API Token not specified"
        absolute_url = os.path.join(AUTHMACHINE_URL, url)

        if payload:
            kwargs['data'] = json.dumps(payload, sort_keys=True)

        if query_params:
            absolute_url += '?' + urlencode(query_params, doseq=True)

        headers = kwargs.pop('headers', {})
        headers['Content-Type'] = 'application/json'
        headers['Authorization'] = 'Token %s' % AUTHMACHINE_API_TOKEN
        response = requests.request(method=method, url=absolute_url, headers=headers, **kwargs)

        return response

    def get_permissions(self, user_id: str) -> List[str]:
        response = self.do_api_request('get', 'api/scim/v1/Users/{}/permissions'.format(user_id),
                                       query_params={'object': ['obj1', 'obj2']})
        if response.status_code == 200:
            data = response.json()
            return data
        else:
            return []

    def check_token_revoked_status(self, token):
        args = {
            'client_id': self.client.client_id,
            'client_secret': self.client.client_secret,
            'access_token': token['access_token'],
            'grant_type': 'check_token_revoked',
        }
        response = requests.request(method="POST",
                                    url=os.path.join(AUTHMACHINE_URL, "oidc/token"),
                                    data=args)

        if response.status_code == 200:
            data = response.json()
            return data
        else:
            return None


app = Flask(__name__)
app.secret_key = b'23487384738748374837'


@app.route('/')
def index():
    if "user_info" in session and "token" in session:
        client = AuthMachineClient()
        token = session["token"]
        user_session = client.check_token_revoked_status(json.loads(token))
        if user_session and user_session["revoked"]:
            clear_user_session()
    return render_template('index.jinja', user_info=session.get('user_info'))


@app.route('/login')
def login():
    client = AuthMachineClient()
    return redirect(client.get_authorization_url())


@app.route('/oidc-callback')
def auth_callback():
    client = AuthMachineClient()
    a_resp = client.get_authorization_response()
    token = client.get_access_token(a_resp)
    session['token'] = token.to_json()
    session['user_info'] = client.get_userinfo(a_resp)
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    client = AuthMachineClient()
    return redirect(client.get_logout_url())


@app.route('/oidc-logout-callback')
def auth_logout_callback():
    clear_user_session()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run()