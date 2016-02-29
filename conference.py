#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.ext import ndb
from google.appengine.api import memcache
from google.appengine.api import taskqueue

from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import TeeShirtSize
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionQueryForm
from models import SessionByDateForm
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import BooleanMessage
from models import ConflictException
from models import StringMessage

from settings import WEB_CLIENT_ID

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED SPEAKER"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
    'EQ':   '=',
    'GT':   '>',
    'GTEQ': '>=',
    'LT':   '<',
    'LTEQ': '<=',
    'NE':   '!='
}

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSpeakerKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api( name='conference',
                version='v1',
                allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
                scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get user id by calling getUserId(user)
        user_id = getUserId(user)
        # step 3. create a new key of kind Profile from the id
        p_key = ndb.Key(Profile, user_id)

        # get the entity from datastore by using get() on the key
        profile = p_key.get()
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            # save the profile to datastore
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
            # TODO 4
            # put the modified profile to datastore
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        print conf
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )

        return request


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                path='queryConferences',
                http_method='POST',
                name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") \
            for conf in conferences]
        )


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # make profile key
        p_key = ndb.Key(Profile, getUserId(user))
        # create ancestor query for this user
        conferences = Conference.query(ancestor=p_key)
        # get the user profile and display name
        prof = p_key.get()
        displayName = getattr(prof, 'displayName')
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName) for conf in conferences]
        )


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        # get user profile
        prof = self._getProfileFromUser()
        # get conferenceKeysToAttend from profile.
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        # Use get_multi(array_of_keys) to fetch all keys at once.
        conferences = ndb.get_multi(conf_keys)

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, "")\
         for conf in conferences]
        )


    # Found on Udacity Forums as this endpoint was not mentioned in Lesson 4 Videos
    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException('No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        print announcement
        return StringMessage(data=announcement)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request, True)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/unregister/{websafeConferenceKey}',
            http_method='POST', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, False)


# - - - Speaker objects - - - - - - - - - - - - - - - - - - - - - - -

    def _createSpeakerObject(self, data):
        """Create Speaker object, return Speaker key"""
        # allocate new Speaker ID
        speaker_id = Speaker.allocate_ids(size=1)[0]
        # make Speaker key fom ID
        speaker_key = ndb.Key(Speaker, speaker_id)

        # Create Speaker and return Speaker key
        speaker = Speaker(name=data,
                          key=speaker_key)
        speaker.put()
        return speaker_key



    def _copySpeakerToForm(self, speaker):
        """Copy relevant fields from Speaker to SpeakerForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
            # convert key to urlsafe
            elif field.name == "websafeSpeakerKey":
                setattr(sf, field.name, speaker.key.urlsafe())
        sf.check_initialized()
        return sf


    @endpoints.method(message_types.VoidMessage, SpeakerForms,
                path='querySpeakers',
                http_method='GET',
                name='querySpeakers')
    def querySpeakers(self, request):
        """ Query for speakers.  Used to get urlsafe Speaker keys,
            which can then be used to query conferences by speaker
        """
        speakers = Speaker.query().order(Speaker.name)

        # return individual SpeakerForm object per Speaker
        return SpeakerForms(
            items=[self._copySpeakerToForm(speaker) \
            for speaker in speakers]
        )


# - - - Session objects - - - - - - - - - - - - - - - - - - - - - - -


    def _createSessionObject(self, request):
        """Create or update Session object, returning SessionForm."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy SpeakerForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # get conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can add sessions.')

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # convert dates and times from strings to Date objects;
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()

        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:5], "%H:%M").time()

        if data['speaker']:
            speaker = Speaker.query()
            speaker = speaker.filter(Speaker.name == data['speaker']).get()
            # Does this speaker already exist?
            if speaker:
                # store existing Speaker key as speaker
                data['speaker'] = speaker.key
            else:
                # create new Speaker and store key
                data['speaker'] = self._createSpeakerObject(data['speaker'])
                speaker = data['speaker'].get()

            # featured speaker task
            taskqueue.add(
                params={'websafeConferenceKey': request.websafeConferenceKey,
                        'websafeSpeakerKey': speaker.key.urlsafe()},
                url='/tasks/update_featured_speaker',
                method='GET'
            )

        # allocate new Session ID with Conference key as parent
        s_id = Session.allocate_ids(size=1, parent=conf.key)[0]
        # make Session key from ID
        s_key = ndb.Key(Session, s_id, parent=conf.key)
        # now I should be able to use s_key.parent() to access the parent Conference as well
        data['key'] = s_key
        del data['websafeConferenceKey']
        del data['websafeKey']


        # create Session & return request
        session_key = Session(**data).put()
        return self._copySessionToForm(session_key.get())


    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert date and time to strings;
                if field.name.endswith('date'):
                    setattr(sf, field.name, str(getattr(session, field.name)))
                elif field.name.endswith('startTime'):
                    setattr(sf, field.name, str(getattr(session, field.name)))
                # convert Speaker url safe key to speaker name
                elif field.name.endswith('speaker'):
                    speaker_key = getattr(session, field.name)
                    if speaker_key is not None:
                        speaker = speaker_key.get()
                        setattr(sf, field.name, speaker.name)
                    else:
                        setattr(sf, field.name, None)
                # just copy others
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            # convert key to urlsafe
            elif field.name == "websafeConferenceKey":
                setattr(sf, field.name, session.key.parent().urlsafe())
            elif field.name == "websafeKey":
                setattr(sf, field.name, session.key.urlsafe())
        sf.check_initialized()
        return sf


    def _doWishlist(self, request, add):
        """Add session to user wishlist."""
        prof = self._getProfileFromUser()  # get user Profile

        # check if session exists given websafeSessionKey
        # get session; check that it exists
        wssk = request.websafeSessionKey
        session_key = ndb.Key(urlsafe=wssk)

        session = session_key.get()
        if not session:
            raise endpoints.NotFoundException(
                'No Session found with key: %s' % wssk)

        entityType = session_key.kind()
        if (entityType != 'Session'):
            raise ConflictException(
                "Can only add Session objects to wishlist")

        if add:
            # check if user already saved this session to wishlist
            if wssk in prof.sessionKeysWishlist:
                raise ConflictException(
                    "This session is already in your wishlist")

            # save this session to user wishlist
            prof.sessionKeysWishlist.append(wssk)
        else:
            if wssk not in prof.sessionKeysWishlist:
                raise ConflictException(
                    "This session is not in your wishlist")

            # remove from wishlist
            prof.sessionKeysWishlist.remove(wssk)

        # write things back to the datastore & return
        prof.put()
        return BooleanMessage(data=True)


    def _getSessionsInWishlist(self, request):
        """Given a Confernce, return all session in user wishlist"""
        prof = self._getProfileFromUser()  # get user Profile

        # Get conference object
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException('No conference found with key: %s' % request.websafeConferenceKey)

        # Get list of session keys from user profile
        session_keys = [ndb.Key(urlsafe=wssk) for wssk in prof.sessionKeysWishlist]

        # get all Sessions in wishlist
        sessions = ndb.get_multi(session_keys)

        # if the Session key parent is not the conference key, remove Session from sessions list
        for session in sessions:
            if session.key.parent() != conf.key:
                sessions.remove(session)

        # return set of Session objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )


    @endpoints.method(SessionForm, SessionForm,
            path='conference/{websafeConferenceKey}/session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)


    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='{websafeConferenceKey}/sessions',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Given a conference, return all sessions"""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException('No conference found with key: %s' % request.websafeConferenceKey)

        # create ancestor query for this conference
        sessions = Session.query(ancestor=conf.key)
        sessions = sessions.order(Session.date)

        # return set of Session objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )


    @endpoints.method(SessionQueryForm, SessionForms,
                path='{websafeConferenceKey}/querySessionsByType',
                http_method='POST',
                name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Given a conference, return all sessions of a specified type"""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException('No conference found with key: %s' % request.websafeConferenceKey)

        sessions = Session.query(ancestor=conf.key)
        sessions = sessions.filter(Session.typeOfSession == request.typeOfSession)
        sessions = sessions.order(Session.date)

        # return set of Session objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )


    @endpoints.method(SPEAKER_GET_REQUEST, SessionForms,
                path='speaker/{websafeSpeakerKey}',
                http_method='POST',
                name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Given a speaker, return all sessions given by this particular speaker"""
        speaker = ndb.Key(urlsafe=request.websafeSpeakerKey)
        if not speaker:
            raise endpoints.NotFoundException(
                'No Speaker found with key: %s' % request.websafeSpeakerKey)
        sessions = Session.query(Session.speaker == speaker)
        sessions = sessions.order(Session.date)

        # return set of Session objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )


    # ADDITIONAL QUERY 1
    @endpoints.method(SessionByDateForm, SessionForms,
                path='{websafeConferenceKey}/sessionsByDate',
                http_method='GET',
                name='getSessionsByDate')
    def getSessionsByDate(self, request):
        """Given a conference and date range, return all sessions"""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey)
        if not conf:
            raise endpoints.NotFoundException(
                'No Conference found with key: %s' % request.websafeConferenceKey)

        # convert start and end date fields to date objects
        if request.startDate:
            request.startDate = datetime.strptime(request.startDate[:10], "%Y-%m-%d").date()
        if request.endDate:
            request.endDate = datetime.strptime(request.endDate[:10], "%Y-%m-%d").date()

        sessions = Session.query(ancestor=conf)
        sessions = sessions.filter(Session.date >= request.startDate)
        sessions = sessions.filter(Session.date <= request.endDate)
        sessions = sessions.order(Session.date)

        # return set of Session objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )


    # ADDITIONAL QUERY 2
    @endpoints.method(CONF_GET_REQUEST, SpeakerForms,
                path='{websafeConferenceKey}/speakers',
                http_method='GET',
                name='getSpeakersInConference')
    def getSpeakersInConference(self, request):
        """Given a conference, return all speakers"""
        conf = ndb.Key(urlsafe=request.websafeConferenceKey)
        if not conf:
            raise endpoints.NotFoundException(
                'No Conference found with key: %s' % request.websafeConferenceKey)

        # get all Sessions in Conference
        sessions = Session.query(ancestor=conf).fetch()
        # initialize empty array to hold speakers
        speakers = []

        # Append unique Speaker objects to speakers array
        for session in sessions:
            if session.speaker is not None:
                speaker = session.speaker.get()
                if speaker not in speakers:
                    speakers.append(speaker)

        # return set of Speaker objects per Speaker
        return SpeakerForms(
            items=[self._copySpeakerToForm(speaker) for speaker in speakers]
        )


    # QUERY RELATED PROBLEM SOLUTION
    @endpoints.method(message_types.VoidMessage, SessionForms,
                path='querysolution',
                http_method='GET',
                name='getSessionsByMultipleInequalities')
    def getSessionsByMultipleInequalities(self, request):
        """Query all sessions before 7pm that are not workshops"""

        # This function is not supposed to be useful
        # It only demonstrates how to filter my multiple inequalities

        # create 7pm time object for filtering
        time = datetime.strptime('19:00', "%H:%M").time()

        sessions = Session.query()
        sessions = sessions.filter(Session.startTime <= time)

        sessionsArray = [session for session in sessions if session.typeOfSession != 'workshop']

        # return set of Session objects per Session
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessionsArray]
        )


    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
                path='addsession/{websafeSessionKey}',
                http_method='POST',
                name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Add session to user's wishlist"""
        return self._doWishlist(request, True)


    @endpoints.method(SESSION_GET_REQUEST, BooleanMessage,
                path='removesession/{websafeSessionKey}',
                http_method='POST',
                name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """Delete session from user's wishlist"""
        return self._doWishlist(request, False)


    @endpoints.method(CONF_GET_REQUEST, SessionForms,
                path='{websafeConferenceKey}/wishlist',
                http_method='POST',
                name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Query for all the sessions in a conference in user wishlist"""
        return self._getSessionsInWishlist(request)


# - - - FEATURED SPEAKERS - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheFeaturedSpeaker(websafeConferenceKey, websafeSpeakerKey):
        """Create Featured Speaker & assign to memcache; used by
        SetFeaturedSpeaker() in main.py.
        """
        speaker_key = ndb.Key(urlsafe=websafeSpeakerKey)
        conf_key = ndb.Key(urlsafe=websafeConferenceKey)

        sessions = Session.query(ancestor=conf_key)
        sessions = sessions.filter(Session.speaker == speaker_key)
        numberOfSessions = sessions.count()

        if (numberOfSessions > 1):
            sessions = sessions.fetch()
            string = "Don't miss out!  %s is speaking as the following conferences: %s" % (
                speaker_key.get().name,
                ', '.join(session.name for session in sessions))
            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, string)


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='speaker/featured/get',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured Speaker string from memcache."""
        featured_speaker = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)
        if not featured_speaker:
            featured_speaker = ""
        print featured_speaker
        return StringMessage(data=featured_speaker)

# registers API
api = endpoints.api_server([ConferenceApi])
