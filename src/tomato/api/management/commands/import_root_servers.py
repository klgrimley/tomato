import json
import logging
import requests
import requests.exceptions
import time
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections, transaction, DatabaseError
from django.utils import timezone
from urllib.parse import urljoin
from ...models import Format, ImportProblem, Meeting, RootServer, ServiceBody


logger = logging.getLogger('django')


class Command(BaseCommand):
    help = 'Updates the meetings database from root servers'

    def handle(self, *args, **options):
        logger.info('starting daemon')

        while True:
            logger.info('retrieving root servers')
            url = 'https://raw.githubusercontent.com/LittleGreenViper/BMLTTally/master/rootServerList.json'
            try:
                root_server_urls = [rs['rootURL'] for rs in json.loads(self.request(url))]
                root_server_urls = [url if url.endswith('/') else url + '/' for url in root_server_urls]
            except Exception as e:
                logger.error('Error retrieving root server list: {}'.format(str(e)))
            else:
                for old in RootServer.objects.exclude(url__in=root_server_urls):
                    try:
                        logger.info('Deleting old root server {}'.format(old.url))
                        old.delete()
                    except Exception as e:
                        logger.error('Error deleting old root server {}'.format(str(e)))

                for url in root_server_urls:
                    logger.info('importing root server {}'.format(url))
                    try:
                        root = RootServer.objects.get_or_create(url=url)[0]
                        ImportProblem.objects.filter(root_server=root).delete()
                        with transaction.atomic():
                            logger.info('importing service bodies')
                            self.update_service_bodies(root)
                            logger.info('importing formats')
                            self.update_formats(root)
                            logger.info('importing meetings')
                            self.update_meetings(root)
                            root.last_successful_import = timezone.now()
                            root.save()
                    except DatabaseError:
                        logger.exception('Encountered DatabaseError, closing database connection')
                        connections.close_all()
                    except Exception as e:
                        logger.error('Error updating root server: {}'.format(str(e)))
            logger.info('sleeping')
            time.sleep(3600 * 4)

    def request(self, url):
        headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:52.0) Gecko/20100101 Firefox/52.0 +tomato'}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            raise Exception('Unexpected status code from root server')
        return response.content

    def update_service_bodies(self, root):
        url = urljoin(root.url, 'client_interface/json/?switcher=GetServiceBodies')
        service_bodies = json.loads(self.request(url))
        ignore_bodies = settings.IGNORE_SERVICE_BODIES.get(root.url)
        if ignore_bodies:
            prev_len = len(service_bodies)
            service_bodies = [sb for sb in service_bodies if int(sb['id']) not in ignore_bodies]
            logger.info('ignored {} service bodies'.format(prev_len - len(service_bodies)))
        ServiceBody.import_from_bmlt_objects(root, service_bodies)

    def update_formats(self, root):
        url = urljoin(root.url, 'client_interface/json/?switcher=GetFormats')
        formats = json.loads(self.request(url))
        Format.import_from_bmlt_objects(root, formats)

    def update_meetings(self, root):
        url = urljoin(root.url, 'client_interface/json/?switcher=GetSearchResults')
        meetings = json.loads(self.request(url))
        ignore_bodies = settings.IGNORE_SERVICE_BODIES.get(root.url)
        if ignore_bodies:
            meetings = [m for m in meetings if int(m['service_body_bigint']) not in ignore_bodies]
        Meeting.import_from_bmlt_objects(root, meetings)
