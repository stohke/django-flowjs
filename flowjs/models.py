import os
import threading
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch.dispatcher import receiver
from django.core.files.storage import default_storage
from django.conf import settings
from settings import FLOWJS_PATH, FLOWJS_REMOVE_FILES_ON_DELETE, FLOWJS_AUTO_DELETE_CHUNKS
from utils import chunk_upload_to


class FlowFile(models.Model):
    """
    A file upload through Flow.js
    """
    STATE_UPLOADING = 1
    STATE_COMPLETED = 2
    STATE_UPLOAD_ERROR = 3

    STATE_CHOICES = [
        (STATE_UPLOADING, "Uploading"),
        (STATE_COMPLETED, "Completed"),
        (STATE_UPLOAD_ERROR, "Upload Error")
    ]

    # identification and file details
    identifier = models.SlugField(max_length=255, unique=True, db_index=True)
    original_filename = models.CharField(max_length=200)
    final_file = models.FileField(upload_to=chunk_upload_to, max_length=255, null=True, blank=True)
    total_size = models.IntegerField(default=0)
    total_chunks = models.IntegerField(default=0)

    # current state
    total_chunks_uploaded = models.IntegerField(default=0)
    state = models.IntegerField(choices=STATE_CHOICES, default=STATE_UPLOADING)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateField(auto_now=True)

    def __unicode__(self):
        return self.identifier

    def update(self):
        self.total_chunks_uploaded = self.chunks.count()
        super(FlowFile, self).save()
        self.join_chunks()

    @property
    def extension(self):
        """
        Return the extension of the upload file
        """
        name, ext = os.path.splitext(self.original_filename)
        return ext

    @property
    def filename(self):
        """
        Return the unique filename generated based on the identifier
        """
        return '%s%s' % (self.identifier, self.extension)

    @property
    def file(self):
        """
        Return the uploaded file
        """
        if self.state == self.STATE_COMPLETED:
            return default_storage.open(self.path)
        return None

    @property
    def path(self):
        """
        Return the path of the file uploaded
        """
        return os.path.join(FLOWJS_PATH, self.filename)

    @property
    def url(self):
        """
        Return the path of the file uploaded
        """
        return os.path.join(settings.MEDIA_URL, FLOWJS_PATH, self.filename)

    def get_chunk_filename(self, number):
        """
        Return the filename of the chunk based on the identifier and chunk number
        """
        return '%s-%s.tmp' % (self.identifier, number)

    def join_chunks(self):
        """
        Join all the chucks in one file
        """
        if self.state == self.STATE_UPLOADING and self.total_chunks_uploaded == self.total_chunks:

            # create file and write chunks in the right order
            temp_file = default_storage.open(self.path, 'wb')
            for chunk in self.chunks.all():
                chunk_bytes = chunk.file.read()
                temp_file.write(chunk_bytes)
            temp_file.close()
            self.final_file = self.path

            # set state as completed
            self.state = self.STATE_COMPLETED
            super(FlowFile, self).save()

            if FLOWJS_AUTO_DELETE_CHUNKS:
                self.delete_chunks()

    def delete_chunks(self, thread=False):
        if not thread:
            t = threading.Thread(target=self.delete_chunks, args=[True])
            t.setDaemon(True)
            t.start()
        else:
            for chunk in self.chunks.all():
                chunk.delete()

    def is_valid_session(self, session):
        """
        Check if a session id is the same that uploaded the file
        """
        return self.identifier.startswith(session)


class FlowFileChunk(models.Model):
    """
    A chunk is part of the file uploaded
    """
    class Meta:
        ordering = ['number']

    # identification and file details
    parent = models.ForeignKey(FlowFile, related_name="chunks")
    file = models.FileField(max_length=255, upload_to=chunk_upload_to)
    number = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __unicode__(self):
        return self.filename

    @property
    def filename(self):
        return self.parent.get_chunk_filename(self.number)

    def save(self, *args, **kwargs):
        super(FlowFileChunk, self).save(*args, **kwargs)
        self.parent.update()


@receiver(pre_delete, sender=FlowFile)
def flow_file_delete(sender, instance, **kwargs):
    """
    Remove files on delete if is activated in settings
    """
    if FLOWJS_REMOVE_FILES_ON_DELETE:
        default_storage.delete(instance.path)


@receiver(pre_delete, sender=FlowFileChunk)
def flow_file_chunk_delete(sender, instance, **kwargs):
    """
    Remove file when chunk is deleted
    """
    instance.file.delete(False)