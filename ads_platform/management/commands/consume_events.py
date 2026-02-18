import json

from django.core.management.base import BaseCommand

from ads_platform.services import get_consumer, parse_event, process_event


class Command(BaseCommand):
    help = "Consume events from Kafka-compatible queue and aggregate into analytics tables"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        consumer = get_consumer()
        batch_size = options["batch_size"]
        while True:
            rows = consumer.poll(batch_size=batch_size)
            if not rows:
                if options["once"]:
                    self.stdout.write(self.style.SUCCESS("No events in queue"))
                    return
                continue
            processed = 0
            for row in rows:
                event = parse_event(json.loads(row))
                if process_event(event):
                    processed += 1
            self.stdout.write(self.style.SUCCESS(f"Processed {processed}/{len(rows)} events"))
            if options["once"]:
                return
