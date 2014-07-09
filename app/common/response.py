# Artshow Keeper: A support tool for keeping an Artshow running.
# Copyright (C) 2014  Ivo Hanak
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
import os
import flask

def renderHtmlTemplate(name, group, language, parameters):
    filePath = '{0}.{1}.{2}.html'.format(name, group, language)
    return flask.render_template(filePath, language=language, **(parameters or {}))

def renderXmlTemplate(name, group, language, parameters):
    filePath = '{0}.{1}.{2}.xml'.format(name, group, language)
    return flask.render_template(filePath, language=language, **(parameters or {}))

def respondHtml(name, group, language, parameters = None):
    return renderHtmlTemplate(name, group, language, parameters)
    
def respondXml(name, group, language, parameters = None):
    xml = renderXmlTemplate(name, group, language, parameters)
    response = flask.make_response(xml)
    response.headers['Content-Type'] = 'text/xml'
    return response
