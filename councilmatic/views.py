import json
import logging as log
from django.contrib.syndication.views import Feed as DjangoFeed
from django.shortcuts import get_object_or_404
from django.views import generic as views
from django.core.cache import cache
from django.core.urlresolvers import reverse_lazy
from haystack.query import SearchQuerySet, RelatedSearchQuerySet
import datetime
from datetime import timedelta

from . import feeds
from . import forms
from phillyleg.models import MetaData_Topic, LegFile, CouncilMember

import haystack.views
import bookmarks.views
import opinions.views
import phillyleg.models
import subscriptions.forms
import subscriptions.models
import subscriptions.views


def get_or_cache(cache_key, getter_func):
    """
    Retrieve a value from the cache. If no value is cached for the key, cache
    and return the value returned by running the getter_func with no arguments.
    """
    val = cache.get(cache_key)
    if val is None:
        val = getter_func()
        cache.set(cache_key, val)
    return val


class NewLegislationFeed (DjangoFeed):
    title = u'New Legislation'
    link = 'http://localhost:8000'
    description = u'Newly introduced legislation'
    max_items = 100

    def items(self):
        return phillyleg.models.LegFile.objects.all().exclude(title='')[:self.max_items]

    def item_title(self, legfile):
        return u'{0.type} {0.id}'.format(legfile)

    def item_pubdate(self, legfile):
        import datetime
        d = legfile.intro_date
        current = datetime.datetime(d.year, d.month, d.day)
        return current


class CustomContentFeedView (DjangoFeed):
    def get_object(self, request, subscription_id):
        return get_object_or_404(subscription.models.Subscription,
                                 pk=subscription_id)

    def title(self, sub):
        return unicode(sub)

    def link(self, sub):
#        return sub.get_absolute_url()
        return u'http://localhost:8000/'

    def description(self, sub):
        return unicode(sub)


class SearchBarMixin (object):
    def get_searchbar_form(self):
        return forms.SimpleSearchForm()

    def get_context_data(self, **kwargs):
        context_data = super(SearchBarMixin, self).get_context_data(**kwargs)
        context_data.update({'searchbar_form': self.get_searchbar_form()})
        return context_data


class BaseDashboardMixin (SearchBarMixin,
                          bookmarks.views.BaseBookmarkMixin):

    def get_recent_legislation(self):
        legfiles = self.get_recent_legislation().prefetch_related('metadata__topics')
        return list(legfiles.exclude(metadata__topics__topic='Routine').order_by('-key')[:6])

    def get_context_data(self, **kwargs):
        search_form = forms.FullSearchForm()

        legfiles = self.get_recent_legislation()
        bookmark_data = self.get_bookmarks_data(legfiles)
        bookmark_cache_key = self.get_bookmarks_cache_key(bookmark_data)

        context_data = super(BaseDashboardMixin, self).get_context_data(
            **kwargs)
        context_data.update({
            'legfiles': legfiles,
            'bookmark_data': bookmark_data,
            'bookmark_cache_key': bookmark_cache_key,
            'search_form': search_form,
        })

        return context_data


class AppDashboardView (BaseDashboardMixin,
                        views.TemplateView):
    template_name = 'councilmatic/dashboard.html'

    def get_recent_legislation(self):
        return phillyleg.models.LegFile.objects.exclude(title='')

    def get_recent_locations(self):
        return list(phillyleg.models.MetaData_Location.objects.\
                       all().filter(valid=True).order_by('-pk')[:10].\
                       prefetch_related('references_in_legislation'))

    def get_recent_topics(self):
        # get the month weeks of legislation
        now = datetime.date.today()
        one_month = datetime.timedelta(days=31)
        date_string = now-one_month

        topic_count_query = """SELECT phillyleg_metadata_topic.id, phillyleg_metadata_topic.topic, Count(phillyleg_legfilemetadata.legfile_id) AS leg_count FROM phillyleg_metadata_topic
        JOIN phillyleg_legfilemetadata_topics ON phillyleg_legfilemetadata_topics.metadata_topic_id = phillyleg_metadata_topic.id
        JOIN phillyleg_legfilemetadata ON phillyleg_legfilemetadata.id = phillyleg_legfilemetadata_topics.legfilemetadata_id
        JOIN phillyleg_legfile ON phillyleg_legfile.key = phillyleg_legfilemetadata.legfile_id
        WHERE phillyleg_legfile.intro_date > '{date_string}' AND phillyleg_metadata_topic.topic != 'Routine'
        GROUP BY phillyleg_metadata_topic.topic, phillyleg_metadata_topic.id
        ORDER BY leg_count DESC""".format(date_string=date_string)

        return list(phillyleg.models.MetaData_Topic.objects.raw(topic_count_query))


    def get_context_data(self, **kwargs):

        recent_topics_query = self.get_recent_topics()
        recent_topics = []

        for t in recent_topics_query:
            percent_width = 100 * (float(t.leg_count) / float(recent_topics_query[0].leg_count))
            recent_topics.append({'topic': t.topic, 'leg_count': t.leg_count, 'percent_width': percent_width})

        context_data = super(AppDashboardView, self).get_context_data(**kwargs)
        context_data['recent_topics'] = recent_topics
        return context_data

class CouncilMembersView(views.TemplateView):
    template_name = 'councilmatic/councilmembers.html'

    def get_councilmembers(self):
        return phillyleg.models.CouncilMember.objects.\
               filter(title__icontains='alderman').\
               exclude(title__icontains='former').order_by('real_name')

    def get_former_councilmembers(self):
        return phillyleg.models.CouncilMember.objects.\
               filter(title__icontains='former').\
               order_by('real_name')

    def get_other_councilmembers(self):
        return phillyleg.models.CouncilMember.objects.\
               exclude(title__icontains='former').exclude(title__icontains='alderman').\
               order_by('real_name')


    def get_context_data(self, **kwargs):
        context_data = super(CouncilMembersView, self).get_context_data(**kwargs)
        context_data['councilmembers'] = self.get_councilmembers()
        context_data['former_councilmembers'] = self.get_former_councilmembers()
        context_data['other_councilmembers'] = self.get_other_councilmembers()
        return context_data


class CouncilMemberDetailView (BaseDashboardMixin,
                               subscriptions.views.SingleSubscriptionMixin,
                               views.DetailView):
    queryset = phillyleg.models.CouncilMember.objects.prefetch_related('tenures', 'tenures__district')
    template_name = 'councilmatic/councilmember_detail.html'

    def get_content_feed(self):
        return feeds.SearchResultsFeed(search_filter={'sponsors': [self.object.name]})

    def get_recent_legislation(self):
        return self.object.legislation

    def get_district(self):
        return self.object.district

    def get_topics(self):
        topic_count_query = """SELECT phillyleg_metadata_topic.id, phillyleg_metadata_topic.topic, Count(phillyleg_legfilemetadata.legfile_id) AS leg_count FROM phillyleg_metadata_topic
        JOIN phillyleg_legfilemetadata_topics ON phillyleg_legfilemetadata_topics.metadata_topic_id = phillyleg_metadata_topic.id
        JOIN phillyleg_legfilemetadata ON phillyleg_legfilemetadata.id = phillyleg_legfilemetadata_topics.legfilemetadata_id
        JOIN phillyleg_legfile ON phillyleg_legfile.key = phillyleg_legfilemetadata.legfile_id
        JOIN phillyleg_legfile_sponsors ON phillyleg_legfile_sponsors.legfile_id = phillyleg_legfile.key
        WHERE phillyleg_metadata_topic.topic != 'Routine' AND phillyleg_legfile_sponsors.councilmember_id = {sponsor_id}
        GROUP BY phillyleg_metadata_topic.topic, phillyleg_metadata_topic.id
        ORDER BY leg_count DESC""".format(sponsor_id=self.object.id)

        return phillyleg.models.MetaData_Topic.objects.raw(topic_count_query)

    def get_context_data(self, **kwargs):
        district = self.get_district()
        context_data = super(CouncilMemberDetailView, self).get_context_data(**kwargs)
        context_data['district'] = district
        context_data['recent_topics'] = self.get_topics()
        return context_data


class SearcherMixin (object):
    def get_search_queryset(self):
        return RelatedSearchQuerySet().load_all_queryset(
            phillyleg.models.LegFile,
            phillyleg.models.LegFile.objects\
                .exclude(title='')\
                .prefetch_related('sponsors', 'metadata__topics', 'metadata__locations'))

    def _init_haystack_searchview(self, request):
        # Construct and run a haystack SearchView so that we can use the
        # resulting values.
        self.search_queryset = self.get_search_queryset()
        self.search_view = haystack.views.SearchView(form_class=forms.FullSearchForm, searchqueryset=self.search_queryset)
        self.search_view.request = request

        self.search_view.form = self.search_view.build_form()
        self.search_view.query = self.search_view.get_query()
        self.search_view.results = self.search_view.get_results().order_by('-order_date')

    def _get_search_results(self, query_params):
        class SQSProxy (object):
            """
            Make a SearchQuerySet look enough like a QuerySet for a ListView
            not to notice the difference.
            """
            def __init__(self, sqs):
                self.sqs = sqs
            def __len__(self):
                return len(self.sqs)
            def __iter__(self):
                return (result.object for result in self.sqs.load_all())
            def __getitem__(self, key):
                if isinstance(key, slice):
                    return [result.object for result in self.sqs.load_all()[key] if result is not None]
                else:
                    return self.sqs[key].object

        if len(query_params) == 0:
            search_queryset = SQSProxy(self.search_queryset)
        else:
            search_queryset = SQSProxy(self.search_view.results)
        return search_queryset


class LegFileListFeedView (SearcherMixin, DjangoFeed):
    def get_object(self, request, *args, **kwargs):
        self._init_haystack_searchview(request)
        query_params = request.GET.copy()
        search_queryset = self._get_search_results(query_params)
        return search_queryset

    def items(self, obj):
        return obj[:100]

    def title(self, obj):
        'testing'

    def link(self):
        return 'http://www.google.com/'


class SearchView (SearcherMixin,
                  SearchBarMixin,
                  subscriptions.views.SingleSubscriptionMixin,
                  bookmarks.views.BaseBookmarkMixin,
                  views.ListView):
    template_name = 'councilmatic/search.html'
    paginate_by = 20
    feed_data = None

    def dispatch(self, request, *args, **kwargs):
        self._init_haystack_searchview(request)
        return super(SearchView, self).dispatch(request, *args, **kwargs)

    def get_content_feed(self):
        search_params = self.request.GET
        return feeds.SearchResultsFeed(search_filter=search_params)

    def get_queryset(self):
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']
        search_queryset = self._get_search_results(query_params)
        return search_queryset

    def get_pages_context_data(self, page_obj, query_params):
        context = {}
        if page_obj:
            context['first_url'] = self.paginated_url(
                1, query_params)

            context['last_url'] = self.paginated_url(
                page_obj.paginator.num_pages, query_params)

            if page_obj.has_next():
                context['next_url'] = self.paginated_url(
                    page_obj.next_page_number(), query_params)

            if page_obj.has_previous():
                context['previous_url'] = self.paginated_url(
                    page_obj.previous_page_number(), query_params)

            page_urls = []
            start_num = max(1, min(page_obj.number - 5,
                                   page_obj.paginator.num_pages - 9))
            end_num = min(start_num + 10, page_obj.paginator.num_pages + 1)

            for page_num in range(start_num, end_num):
                if page_num != page_obj.number:
                    url = self.paginated_url(page_num, query_params)
                else:
                    url = None
                page_urls.append((page_num, url))
            context['page_urls'] = page_urls
        return context

    def get_context_data(self, **kwargs):
        """
        Generates the actual HttpResponse to send back to the user.
        """
        context = super(SearchView, self).get_context_data(**kwargs)
        context['form'] = self.search_view.form

        page_obj = context.get('page_obj', None)
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']

        context.update(self.get_pages_context_data(page_obj, query_params))

        bookmark_data = self.get_bookmarks_data(page_obj.object_list)
        bookmark_cache_key = self.get_bookmarks_cache_key(bookmark_data)

        context['bookmark_cache_key'] = bookmark_cache_key
        context['bookmark_data'] = bookmark_data

        context['topics'] = get_or_cache('search_topics',
            lambda: [(topic.topic, topic.topic)
                     for topic in MetaData_Topic.objects.all().order_by('topic')])
        context['statuses'] = get_or_cache('search_statuses',
            lambda: legfile_choices('status'))
        context['controlling_bodies'] = get_or_cache('search_controlling_bodies',
            lambda: legfile_choices('controlling_body'))
        context['file_types'] = get_or_cache('search_file_types',
            lambda: legfile_choices('type'))
        context['sponsors'] = get_or_cache('search_sponsors',
            lambda: [(member.real_name, member.real_name)
                     for member in CouncilMember.objects.all().order_by('real_name')])
        
        log.debug(context)
        return context

    def paginated_url(self, page_num, query_params):
        url = '{0}?page={1}'.format(self.request.path, page_num)
        if query_params:
            url += '&' + query_params.urlencode()
        return url


class LegislationStatsMixin (object):
    def get_queryset(self):
        queryset = super(LegislationStatsMixin, self).get_queryset()

        now = datetime.date.today()
        four_weeks = datetime.timedelta(days=28)
        queryset = queryset.filter(intro_date__gte=now-four_weeks)
        return queryset


class LegislationListView (views.RedirectView):
    url = reverse_lazy('search')


class LegislationDetailView (SearchBarMixin,
                             subscriptions.views.SingleSubscriptionMixin,
                             bookmarks.views.SingleBookmarkedObjectMixin,
                             opinions.views.SingleOpinionTargetMixin,
                             views.DetailView):
    model = phillyleg.models.LegFile
    template_name = 'councilmatic/legfile_detail.html'

    def get_queryset(self):
        """Select all the data relevant to the legislation."""
        return self.model.objects\
                   .all().select_related('metadata')\
                   .prefetch_related('actions', 'attachments', 'sponsors',
                                     'references_in_legislation',
                                     'metadata__locations',
                                     'metadata__mentioned_legfiles',
                                     'metadata__topics')

    def get_content_feed(self):
        legfile = self.object
        return feeds.LegislationUpdatesFeed(pk=legfile.pk)

#    def on_object_gotten(self, legfile):
#        # Construct the feed_data factory
#        if not self.feed_data:
#            self.feed_data = lambda: feeds.LegislationUpdatesFeed(pk=legfile.pk)

#    def get_object(self):
#        legfile = super(LegislationDetailView, self).get_object()

#        # Intercept the object
#        self.on_object_gotten(legfile)

#        return legfile


def legfile_choices(field):
    value_objs = LegFile.objects.values(field).distinct().order_by(field)
    values = [(value_obj[field], value_obj[field])
          for value_obj in value_objs]
    return values
