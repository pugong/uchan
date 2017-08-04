from typing import List, Optional

from sqlalchemy.orm import load_only, joinedload

from uchan.lib import validation
from uchan.lib.cache import cache, cache_key, LocalCache
from uchan.lib.database import session
from uchan.lib.exceptions import ArgumentError
from uchan.lib.model import BoardModel, BoardConfigModel
from uchan.lib.ormmodel import BoardOrmModel

MESSAGE_DUPLICATE_BOARD_NAME = 'Duplicate board name'
MESSAGE_INVALID_NAME = 'Invalid board name'


def create(board: BoardModel) -> BoardModel:
    if not validation.check_board_name_validity(board.name):
        raise ArgumentError(MESSAGE_INVALID_NAME)

    with session() as s:
        existing = s.query(BoardOrmModel).filter_by(name=board.name).one_or_none()
        if existing:
            raise ArgumentError(MESSAGE_DUPLICATE_BOARD_NAME)

        orm_board = board.to_orm_model()

        board_config = BoardConfigModel.from_defaults()
        board_config_orm = board_config.to_orm_model()
        s.add(board_config_orm)
        s.flush()

        orm_board.config_id = board_config_orm.id

        s.add(orm_board)
        s.commit()

        board = board.from_orm_model(orm_board)

        cache.set(cache_key('board_and_config', board.name), board.to_cache())

        _set_all_board_names_cache(s)

        s.commit()

        return board


def update_config(board: BoardModel):
    with session() as s:
        s.merge(board.config.to_orm_model())
        s.commit()
        cache.set(cache_key('board_and_config', board.name), board.to_cache())


def get_all() -> List[BoardModel]:
    with session() as s:
        b = s.query(BoardOrmModel).order_by(BoardOrmModel.name).all()
        res = list(map(lambda i: BoardModel.from_orm_model(i), b))
        s.commit()
        return res


local_cache = LocalCache()


def get_all_board_names() -> List[str]:
    local_cached = local_cache.get('all_board_names')
    if local_cached:
        return local_cached

    all_board_names_cached = cache.get(cache_key('all_board_names'))
    if all_board_names_cached is not None:
        # No need to map a list of strings
        res = all_board_names_cached
    else:
        with session() as s:
            q = s.query(BoardOrmModel).options(load_only('name')).order_by(BoardOrmModel.name)
            # No mapping here either
            res = list(map(lambda i: i.name, q.all()))
            s.commit()

        cache.set(cache_key('all_board_names'), res)

    local_cache.set('all_board_names', res)

    return res


def find_by_name(name: str) -> Optional[BoardModel]:
    if not validation.check_board_name_validity(name):
        raise ArgumentError(MESSAGE_INVALID_NAME)

    board_cache = cache.get(cache_key('board_and_config', name))
    if not board_cache:
        with session() as s:
            q = s.query(BoardOrmModel).filter_by(name=name)
            q = q.options(joinedload('config'))
            board_orm_model = q.one_or_none()
            if not board_orm_model:
                return None
            board = BoardModel.from_orm_model(board_orm_model, include_config=True)
            cache.set(cache_key('board_and_config', name), board.to_cache())
            return board

    return BoardModel.from_cache(board_cache)


def find_by_names(names: List[str]) -> List[BoardModel]:
    """unknown names are ignored!"""

    for name in names:
        if not validation.check_board_name_validity(name):
            raise ArgumentError(MESSAGE_INVALID_NAME)

    boards = []
    with session() as s:
        for name in names:
            board_cache = cache.get(cache_key('board_and_config', name))
            if board_cache:
                boards.append(BoardModel.from_cache(board_cache))
            else:
                board_orm_model = s.query(BoardOrmModel).filter_by(name=name).one_or_none()
                if board_orm_model:
                    board = BoardModel.from_orm_model(board_orm_model, include_config=True)
                    cache.set(cache_key('board_and_config', name), board.to_cache())
                    boards.append(board)

    return boards


def delete(board: BoardModel):
    with session() as s:
        b = s.query(BoardOrmModel).filter_by(id=board.id).one()
        s.delete(b)
        s.commit()

        # The pages etc. will fall out of the cache themselves
        # This is the first thing all board related endpoints use, so they get cancelled at the start of the request.
        # If any are still working with caches of this board let them use the left over caches.
        cache.delete(cache_key('board_and_config', board.name))

        _set_all_board_names_cache(s)

        s.commit()


def _set_all_board_names_cache(s):
    all_board_names_q = s.query(BoardOrmModel).options(load_only('name')).order_by(BoardOrmModel.name)
    cache.set(cache_key('all_board_names'), list(map(lambda i: i.name, all_board_names_q.all())))