App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting
   your local server's address (by default [localhost:8080][5].)
1. Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.

## Design Choices
- Session objects use ndb.KeyProperty to establish a relationship with their associate Speaker object.  This makes it easy to query sessions given a Speaker object, and to get Speaker details given a Session object
- Session keys desgnate associated Conference keys as their parent.  This creates an ancestor relationship between the parent Conference and all child Session objects, and makes it easy to find sessions for a given conference.
- ADDITIONAL FUNCTIONALITY: Speakers are entities.  This allows for flexibility of adding more speaker properties later on (description, areas of expertise, photo, etc).

## Additional Queries
1. **getSpeakersInConference**  Given a Conference, this query returns all participating Speakers.
1. **getSessionsByDate**  Given a Conference and start / end date, this query returns all Sessions occuring within the specified date range.

## Query Related Problem
"Let’s say that you don't like workshops and you don't like sessions after 7 pm. How would you handle a query for all non-workshop sessions before 7 pm? What is the problem for implementing this query? What ways to solve it did you think of?"

### The problem:
For performance reasons, the datastore query mechanism iterates along one index.  As a result, an inequality filter (<, <=, >, >=, !=) can only be applied to at most one property.

### One solution
Query all sessions, then post-filter for sessions before 7pm (use a python if statement to filter the second inequality).  This could be done vise versa as well (query for non workshops, use an if statement for time filter)

You can see an example of this solution in the "getSessionsByMultipleInequalities" endpoint function.  For real life cases, you would create a function to dynamically handle this type of inequality.  This example function is for demonstration purposes only.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
