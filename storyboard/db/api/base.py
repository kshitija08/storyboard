# Copyright (c) 2014 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy

from oslo_config import cfg
from oslo_db import exception as db_exc
from oslo_db.sqlalchemy import session as db_session
from oslo_db.sqlalchemy.utils import InvalidSortKey
from oslo_db.sqlalchemy.utils import paginate_query as utils_paginate_query
from oslo_log import log
from pecan import request
import six
from sqlalchemy import and_, or_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.expression import false, true
import sqlalchemy.types as sqltypes

from storyboard._i18n import _
from storyboard.common import exception as exc
from storyboard.db import models

CONF = cfg.CONF
LOG = log.getLogger(__name__)
_FACADE = None

BASE = models.Base


def _get_facade_instance():
    """Generate an instance of the DB Facade.
    """
    global _FACADE

    try:
        if _FACADE is None:
            _FACADE = db_session.EngineFacade.from_config(CONF)
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()

    return _FACADE


def _destroy_facade_instance():
    """Destroys the db facade instance currently in use.
    """
    global _FACADE
    _FACADE = None


def apply_query_filters(query, model, **kwargs):
    """Parses through a list of kwargs to determine which exist on the model,
    which should be filtered as ==, and which should be filtered as LIKE
    """

    for k, v in six.iteritems(kwargs):
        if v and hasattr(model, k):
            column = getattr(model, k)
            if column.is_attribute:
                if isinstance(v, list):
                    # List() style parameters receive WHERE IN logic.
                    query = query.filter(column.in_(v))
                elif isinstance(column.type, sqltypes.String):
                    # Filter strings with LIKE
                    query = query.filter(column.like("%" + v + "%"))
                else:
                    # Everything else is a strict equal
                    query = query.filter(column == v)

    return query


def get_engine():
    """Returns the global instance of our database engine.
    """
    facade = _get_facade_instance()

    try:
        return facade.get_engine(use_slave=True)
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()


def paginate_query(query, model, limit, sort_key, marker=None,
                   offset=None, sort_dir=None, sort_dirs=None):
    if offset is not None:
        # If we are doing offset-based pagination, don't set a
        # limit or a marker.
        # FIXME: Eventually the webclient should be smart enough
        # to do marker-based pagination, at which point this will
        # be unnecessary.
        start, end = (offset, offset + limit)
        limit, marker = (None, None)
    try:
        sorted_query = utils_paginate_query(query=query,
                                            model=model,
                                            limit=limit,
                                            sort_keys=[sort_key],
                                            marker=marker,
                                            sort_dir=sort_dir,
                                            sort_dirs=sort_dirs)
        if offset is not None:
            return sorted_query.slice(start, end)
        return sorted_query
    except ValueError as ve:
        raise exc.DBValueError(message=str(ve))
    except InvalidSortKey:
        raise exc.DBInvalidSortKey(_("Invalid sort_field [%s]") %
                                   sort_key)


def get_session(autocommit=True, expire_on_commit=False, in_request=True,
                **kwargs):
    """Returns a database session from our facade.
    """
    facade = _get_facade_instance()
    try:
        if in_request:
            return request.session
        else:
            # Ok, no request, just return a new session
            return facade.get_session(
                autocommit=autocommit,
                expire_on_commit=expire_on_commit, **kwargs)
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()


def cleanup():
    """Manually clean up our database engine.
    """
    try:
        _destroy_facade_instance()
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()


def model_query(model, session=None):
    """Query helper.

    :param model: base model to query
    """
    session = session or get_session()

    try:
        query = session.query(model)
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.ColumnError:
        raise exc.ColumnError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()
    except db_exc.DBInvalidUnicodeParameter:
        raise exc.DBInvalidUnicodeParameter()
    return query


def __entity_get(kls, entity_id, session):
    try:
        query = model_query(kls, session)
        return query.filter_by(id=entity_id).first()
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.ColumnError:
        raise exc.ColumnError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()
    except db_exc.DBInvalidUnicodeParameter:
        raise exc.DBInvalidUnicodeParameter()


def entity_get(kls, entity_id, filter_non_public=False, session=None):
    if not session:
        session = get_session()

    entity = __entity_get(kls, entity_id, session)

    if filter_non_public:
        entity = _filter_non_public_fields(entity, entity._public_fields)

    return entity


def entity_get_all(kls, filter_non_public=False, marker=None, offset=None,
                   limit=None, sort_field='id', sort_dir='asc', session=None,
                   **kwargs):
    # Sanity checks, in case someone accidentally explicitly passes in 'None'
    if not sort_field:
        sort_field = 'id'
    if not sort_dir:
        sort_dir = 'asc'

    # Construct the query
    query = model_query(kls, session)

    # Sanity check on input parameters
    query = apply_query_filters(query=query, model=kls, **kwargs)

    # Construct the query
    try:
        query = paginate_query(query=query,
                               model=kls,
                               limit=limit,
                               sort_key=sort_field,
                               marker=marker,
                               offset=offset,
                               sort_dir=sort_dir)

        # Execute the query
        entities = query.all()
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()
    except db_exc.DBInvalidUnicodeParameter:
        raise exc.DBInvalidUnicodeParameter()

    if len(entities) > 0 and filter_non_public:
        sample_entity = entities[0]
        public_fields = getattr(sample_entity, "_public_fields", [])

        entities = [_filter_non_public_fields(entity, public_fields)
                    for entity in entities]

    return entities


def entity_get_count(kls, session=None, **kwargs):
    # Construct the query
    query = model_query(kls, session)

    # Sanity check on input parameters
    query = apply_query_filters(query=query, model=kls, **kwargs)

    try:
        count = query.count()
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()
    except db_exc.DBInvalidUnicodeParameter:
        raise exc.DBInvalidUnicodeParameter()

    return count


def _filter_non_public_fields(entity, public_list=list()):
    ent_copy = copy.copy(entity)

    for attr_name, val in six.iteritems(entity.__dict__):
        if attr_name.startswith("_"):
            continue

        if attr_name not in public_list:
            delattr(ent_copy, attr_name)

    return ent_copy


def entity_create(kls, values, session=None):
    entity = kls()
    entity.update(values.copy())

    if not session:
        session = get_session()

    try:
        with session.begin(subtransactions=True):
            session.add(entity)
        session.expunge(entity)

    except db_exc.DBDuplicateEntry as de:
        raise exc.DBDuplicateEntry(object_name=kls.__name__,
                                   value=de.value)
    except db_exc.DBReferenceError as re:
        raise exc.DBReferenceError(object_name=kls.__name__,
                                   value=re.constraint, key=re.key)
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.ColumnError:
        raise exc.ColumnError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()
    except db_exc.DBInvalidUnicodeParameter:
        raise exc.DBInvalidUnicodeParameter

    return entity


def entity_update(kls, entity_id, values, session=None,
                  filter_non_public=False):
    if not session:
        session = get_session()

    try:
        with session.begin(subtransactions=True):
            entity = __entity_get(kls, entity_id, session)
            if entity is None:
                raise exc.NotFound(_("%(name)s %(id)s not found") %
                                   {'name': kls.__name__, 'id': entity_id})

            values_copy = values.copy()
            values_copy["id"] = entity_id
            entity.update(values_copy)
            session.add(entity)
        session.expunge(entity)

    except db_exc.DBDuplicateEntry as de:
        raise exc.DBDuplicateEntry(object_name=kls.__name__,
                                   value=de.value)
    except db_exc.DBReferenceError as re:
        raise exc.DBReferenceError(object_name=kls.__name__,
                                   value=re.constraint, key=re.key)
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.ColumnError:
        raise exc.ColumnError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()
    except db_exc.DBInvalidUnicodeParameter:
        raise exc.DBInvalidUnicodeParameter

    session = get_session()
    entity = __entity_get(kls, entity_id, session)

    if filter_non_public:
        entity = _filter_non_public_fields(entity, entity._public_fields)

    return entity


def entity_hard_delete(kls, entity_id, session=None):
    if not session:
        session = get_session()

    try:
        with session.begin(subtransactions=True):
            query = model_query(kls, session)
            entity = query.filter_by(id=entity_id).first()
            if entity is None:
                raise exc.NotFound(_("%(name)s %(id)s not found") %
                                   {'name': kls.__name__, 'id': entity_id})

            session.delete(entity)

    except db_exc.DBReferenceError as re:
        raise exc.DBReferenceError(object_name=kls.__name__,
                                   value=re.constraint, key=re.key)
    except db_exc.DBConnectionError:
        raise exc.DBConnectionError()
    except db_exc.ColumnError:
        raise exc.ColumnError()
    except db_exc.DBDeadlock:
        raise exc.DBDeadLock()
    except db_exc.DBInvalidUnicodeParameter:
        raise exc.DBInvalidUnicodeParameter()


def filter_private_stories(query, current_user, story_model=models.Story):
    """Takes a query and filters out stories the user shouldn't see.

    :param query: The query to be filtered.
    :param current_user: The ID of the user requesting the result.
    :param story_model: The database model used for stories in the query.

    """
    # First filter based on users with permissions set directly
    query = query.outerjoin(models.story_permissions,
                            models.Permission,
                            models.user_permissions,
                            models.User)
    if current_user:
        visible_to_users = query.filter(
            or_(
                and_(
                    models.User.id == current_user,
                    story_model.private == true()
                ),
                story_model.private == false(),
                story_model.id.is_(None)
            )
        )
    else:
        visible_to_users = query.filter(
            or_(
                story_model.private == false(),
                story_model.id.is_(None)
            )
        )

    # Now filter based on membership of teams with permissions
    users = aliased(models.User, name="story_users")
    query = query.outerjoin(models.team_permissions,
                            models.Team,
                            models.team_membership,
                            (users,
                             users.id == models.team_membership.c.user_id))
    if current_user:
        visible_to_teams = query.filter(
            or_(
                and_(
                    users.id == current_user,
                    story_model.private == true()
                ),
                story_model.private == false(),
                story_model.id.is_(None)
            )
        )
    else:
        visible_to_teams = query.filter(
            or_(
                story_model.private == false(),
                story_model.id.is_(None)
            )
        )

    return visible_to_users.union(visible_to_teams)


def filter_private_worklists(query, current_user, hide_lanes=True):
    """Takes a query and filters out worklists the user shouldn't see.

    :param query: The query to be filtered.
    :param current_user: The ID of the user requesting the result.
    :param hide_lanes: Whether or not to also filter out worklists which
    are lanes in a board.

    """
    # Alias all the things we're joining, in case they've been
    # joined already.
    board_worklists = aliased(models.BoardWorklist,
                              name="worklist_boardworklists")
    boards = aliased(models.Board, name="worklist_boards")
    board_permissions = aliased(models.board_permissions,
                                name="worklist_boardpermissions")
    worklist_permissions = aliased(models.worklist_permissions,
                                   name="worklist_worklistpermissions")
    permissions = aliased(models.Permission, name="worklist_permissions")
    user_permissions = aliased(models.user_permissions,
                               name="worklist_userpermissions")
    users = aliased(models.User, name="worklist_users")

    # Worklists permissions must be inherited from the board which
    # contains the list (if any). To handle this we split the query
    # into the lists which are in boards (`lanes`) and those which
    # aren't (`lists`). We then either hide the lanes entirely or
    # unify the two queries.
    lanes = query.join(
        (board_worklists, models.Worklist.id == board_worklists.list_id))
    lanes = (lanes
        .outerjoin((boards, boards.id == board_worklists.board_id))
        .outerjoin((board_permissions,
                    boards.id == board_permissions.c.board_id))
        .outerjoin((permissions,
                    permissions.id == board_permissions.c.permission_id))
        .outerjoin((user_permissions,
                    permissions.id == user_permissions.c.permission_id))
        .outerjoin((users, user_permissions.c.user_id == users.id)))

    lists = query.outerjoin(
        (board_worklists, models.Worklist.id == board_worklists.list_id))
    lists = (lists.filter(board_worklists.board_id.is_(None))
        .outerjoin((worklist_permissions,
                    models.Worklist.id == worklist_permissions.c.worklist_id))
        .outerjoin((permissions,
                    permissions.id == worklist_permissions.c.permission_id))
        .outerjoin((user_permissions,
                    user_permissions.c.permission_id == permissions.id))
        .outerjoin((users, user_permissions.c.user_id == users.id)))

    if current_user:
        if not hide_lanes:
            lanes = lanes.filter(
                or_(
                    and_(
                        users.id == current_user,
                        boards.private == true()
                    ),
                    boards.private == false()
                )
            )
        lists = lists.filter(
            or_(
                and_(
                    users.id == current_user,
                    models.Worklist.private == true()
                ),
                models.Worklist.private == false(),
                models.Worklist.id.is_(None)
            )
        )
    else:
        if not hide_lanes:
            lanes = lanes.filter(boards.private == false())
        lists = lists.filter(
            or_(
                models.Worklist.private == false(),
                models.Worklist.private.is_(None)
            )
        )

    if hide_lanes:
        return lists
    return lists.union(lanes)


def filter_private_boards(query, current_user):
    """Takes a query and filters out the boards that the user should not see.

    :param query: The query to be filtered.
    :param current_user: The ID of the user requesting the result.

    """
    board_permissions = aliased(models.board_permissions,
                                name="board_boardpermissions")
    permissions = aliased(models.Permission, name="board_permissions")
    user_permissions = aliased(models.user_permissions,
                               name="board_userpermissions")
    users = aliased(models.User, name="board_users")

    query = (query
        .outerjoin((board_permissions,
                    models.Board.id == board_permissions.c.board_id))
        .outerjoin((permissions,
                    board_permissions.c.permission_id == permissions.id))
        .outerjoin((user_permissions,
                    permissions.id == user_permissions.c.permission_id))
        .outerjoin((users, user_permissions.c.user_id == users.id)))

    if current_user:
        query = query.filter(
            or_(
                and_(
                    users.id == current_user,
                    models.Board.private == true()
                ),
                models.Board.private == false(),
                models.Board.id.is_(None)
            )
        )
    else:
        query = query.filter(
            or_(
                models.Board.private == false(),
                models.Board.id.is_(None)
            )
        )

    return query
