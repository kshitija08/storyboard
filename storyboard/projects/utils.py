# Copyright 2013 Thierry Carrez <thierry@openstack.org>
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from storyboard.projects.models import Project
from storyboard.projects.models import ProjectGroup


def retrieve_projects(name, group):
    if group:
        ref = ProjectGroup.objects.get(name=name)
        return ref, ref.members.all()
    else:
        ref = Project.objects.get(name=name)
        return ref, [ref]
