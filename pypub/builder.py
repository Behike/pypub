"""
Epub Book Generator
"""
import os
import uuid
import shutil
import zipfile
import tempfile
import logging
from logging import Logger
from datetime import datetime
from dataclasses import dataclass, field
from typing import NamedTuple, Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont
from jinja2 import Environment, FileSystemLoader, Template

from .chapter import Chapter
from .factory import ChapterFactory, SimpleChapterFactory

#** Variables **#
__all__ = ['AssignedChapter', 'Assignment', 'EpubSpec', 'EpubBuilder']

#: assigned chapter type definition
AssignedChapter = Tuple['Assignment', Chapter]

#: base library directory
BASE = os.path.dirname(__file__)

#: static directory path
STATIC = os.path.join(BASE, 'static/')

#: templates directory to render content from
TEMPLATES = os.path.join(BASE, 'templates/')

#: jinja2 environment to load templates
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES))

#: default logging instance for library
default_logger = logging.getLogger('pypub')

#** Functions **#

def epub_dirs(basedir: Optional[str] = None) -> 'EpubDirs':
    """generate directories for epub data"""
    basedir = basedir or tempfile.mkdtemp()
    oebps   = os.path.join(basedir, 'OEBPS')
    metainf = os.path.join(basedir, 'META-INF')
    images  = os.path.join(oebps, 'images')
    styles  = os.path.join(oebps, 'styles')
    os.makedirs(oebps)
    os.makedirs(metainf)
    os.makedirs(images)
    os.makedirs(styles)
    return EpubDirs(basedir, oebps, metainf, images, styles)

def copy_file(src: str, into: str):
    """copy filepath into the `into` directory"""
    fname = os.path.basename(src)
    dst   = os.path.join(into, fname)
    shutil.copyfile(src, dst)

def copy_static(fpath: str, into: str):
    """copy static filepath into the `into` directory"""
    src = os.path.join(STATIC, fpath)
    copy_file(src, into)

def get_extension(fname: str) -> str:
    """get extension of the given filename"""
    return fname.rsplit('.', 1)[-1]

def render_template(name: str, into: str, encoding: str, kwargs: dict):
    """render template in templates directory"""
    fname    = name.rsplit('.j2', 1)[0]
    dest     = os.path.join(into, os.path.basename(fname))
    template = jinja_env.get_template(name)
    content  = template.render(**kwargs)
    with open(dest, 'w', encoding=encoding) as f:
        f.write(content)

#** Classes **#

@dataclass
class Assignment:
    id:         str
    link:       str
    play_order: int

@dataclass
class EpubDirs:
    basedir: str
    oebps:   str
    metainf: str
    images:  str
    styles:  str

class MimeFile(NamedTuple):
    name: str
    mime: str

@dataclass
class EpubSpec:
    """
    Epub Builder Specification
    """
    title:     str
    creator:   str            = 'pypub'
    subtitle:  str            = ''
    language:  str            = 'en'
    rights:    str            = ''
    publisher: str            = 'pypub'
    encoding:  str            = 'utf-8'
    date:      datetime       = field(default_factory=datetime.now)
    epub_dir:  Optional[str]  = None
    factory:   ChapterFactory = field(repr=False, default_factory=SimpleChapterFactory)
    logger:    Logger         = field(repr=False, default=default_logger)
    css_paths: List[str]      = field(repr=False, default_factory=list)

class EpubBuilder:
    """
    Epub Builder Class for Constructing Epub Books
    """

    def __init__(self, epub: EpubSpec):
        self.uid      = str(uuid.uuid4())
        self.epub     = epub
        self.logger   = epub.logger
        self.factory  = epub.factory
        self.encoding = epub.encoding
        self.dirs:     Optional[EpubDirs]    = None
        self.template: Optional[Template]    = None
        self.chapters: List[AssignedChapter] = []
    
    def __enter__(self):
        """begin epub building in context-manager"""
        self.begin()
        return self

    def __exit__(self, *_):
        """cleanup on exit of context-manager"""
        self.cleanup()

    def begin(self) -> EpubDirs:
        """begin building operations w/ basic file structure"""
        # read jinja2 chapter template
        if not self.template:
            self.template = jinja_env.get_template('page.xhtml.j2')
        if self.dirs:
            return self.dirs
        
        # generate base directories and copy static files
        self.dirs = epub_dirs(self.epub.epub_dir)
        copy_static('mimetype', self.dirs.basedir)
        copy_static('container.xml', self.dirs.metainf)
        copy_static('css/coverpage.css', self.dirs.styles)
        copy_static('css/styles.css', self.dirs.styles)
        for path in self.epub.css_paths:
            copy_file(path, self.dirs.styles)
        fpath    = os.path.join(self.dirs.oebps, 'coverpage.xhtml')
        kwargs = {
            'epub':     self.epub, 
            'styles':   os.listdir(self.dirs.styles),
        }
        # content  = template.render(**kwargs)
        with open(fpath, 'w', encoding=self.encoding) as f:
            cover = self.template.render(**kwargs)
            f.write(cover)
        return self.dirs

    def render_chapter(self, assign: Assignment, chapter: Chapter):
        """render an assigned chapter into the ebook"""
        if not self.dirs or not self.template:
            raise RuntimeError('cannot render_chapter before `begin`')
        # log chapter generation
        self.chapters.append((assign, chapter))
        self.logger.debug('rendering chapter #%d: %r' % (
            assign.play_order, chapter.title))
        # render chapter w/ appropriate kwargs
        args    = (self.logger, chapter, self.dirs.images, self.template)
        kwargs  = {'epub': self.epub, 'chapter': chapter}
        fpath   = os.path.join(self.dirs.oebps, assign.link)
        content = self.factory.render(*args, kwargs)
        with open(fpath, 'wb') as f:
            f.write(content)

    def index(self):
        """build index files for epub before finalizing"""
        if not self.dirs:
            raise RuntimeError('cannot index epub before `begin`')
        kwargs = {
            'uid':      self.uid,
            'epub':     self.epub, 
            'styles':   os.listdir(self.dirs.styles),
            'chapters': self.chapters,
            'images':   [
                MimeFile(fname, get_extension(fname))
                for fname in os.listdir(self.dirs.images)
            ]
        }
        # render and write the rest of the templates
        self.logger.debug('epub=%r, writing final templates' % self.epub.title)
        render_template('book.ncx.j2', self.dirs.oebps, self.encoding, kwargs)
        render_template('book.opf.j2', self.dirs.oebps, self.encoding, kwargs)

    def compress(self, fpath: Optional[str] = None) -> str:
        """compress build and zip content into epub"""
        if not self.dirs:
            raise RuntimeError('cannot finalize before `begin`')
        # reformat and build fpath w/ defaults
        fname = os.path.basename(fpath) if fpath else self.epub.title
        fname = fname.rsplit('.epub', 1)[0] + '.epub'
        fpath = os.path.dirname(fpath) if fpath else '.'
        fpath = os.path.join(fpath, fname)
        fzip  = fpath.rsplit('.epub', 1)[0] + '.zip'
        # zip contents of directory 
        self.logger.debug('epub=%r, zipping content' % self.epub.title)
        zipf = zipfile.ZipFile(fzip, 'w', zipfile.ZIP_DEFLATED)
        for root, _, files in os.walk(self.dirs.basedir):
            relpath = root.split(self.dirs.basedir, 1)[1]
            for file in files:
                real   = os.path.join(root, file)
                local  = os.path.join(relpath, file)
                method = zipfile.ZIP_STORED if file == 'mimetype' else None
                zipf.write(real, local, method)
        # rename zip to epub
        zipf.close()
        os.rename(fzip, fpath)
        return fpath

    def finalize(self, fpath: Optional[str] = None) -> str:
        """finalize build and index and compress epub directory"""
        self.index()
        return self.compress(fpath)

    def cleanup(self):
        """cleanup leftover resources after finalization"""
        if self.dirs:
            shutil.rmtree(self.dirs.basedir, ignore_errors=True)
            self.dirs = None
