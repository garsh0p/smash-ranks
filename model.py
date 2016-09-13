from bson.objectid import ObjectId

import trueskill

import orm

SOURCE_TYPE_CHOICES = ('tio', 'challonge', 'smashgg', 'other')

# Embedded documents


class AliasMapping(orm.Document):
    fields = [('player_id', orm.ObjectIDField()),
              ('player_alias', orm.StringField(required=True))]


class AliasMatch(orm.Document):
    fields = [('winner', orm.StringField(required=True)),
              ('loser', orm.StringField(required=True))]


class Match(orm.Document):
    fields = [('winner', orm.ObjectIDField(required=True)),
              ('loser', orm.ObjectIDField(required=True))]

    def __str__(self):
        return "%s > %s" % (self.winner, self.loser)

    def contains_players(self, player1, player2):
        return (self.winner == player1 and self.loser == player2) or \
               (self.winner == player2 and self.loser == player1)

    def contains_player(self, player_id):
        return self.winner == player_id or self.loser == player_id

    def did_player_win(self, player_id):
        return self.winner == player_id

    def get_opposing_player_id(self, player_id):
        if self.winner == player_id:
            return self.loser
        elif self.loser == player_id:
            return self.winner
        else:
            return None


class RankingEntry(orm.Document):
    fields = [('player', orm.ObjectIDField(required=True)),
              ('rank', orm.IntField(required=True)),
              ('rating', orm.FloatField(required=True))]


class Rating(orm.Document):
    fields = [('mu', orm.FloatField(required=True, default=25.)),
              ('sigma', orm.FloatField(required=True, default=25. / 3))]

    def trueskill_rating(self):
        return trueskill.Rating(mu=self.mu, sigma=self.sigma)

    @classmethod
    def from_trueskill(cls, trueskill_rating):
        return Rating(mu=trueskill_rating.mu,
                      sigma=trueskill_rating.sigma)


# MongoDB collection documents

MONGO_ID_SELECTOR = {'db': '_id',
                     'web': 'id'}


class Player(orm.Document):
    fields = [('id', orm.ObjectIDField(required=True, load_from=MONGO_ID_SELECTOR,
                                       dump_to=MONGO_ID_SELECTOR)),
              ('name', orm.StringField(required=True)),
              ('aliases', orm.ListField(orm.StringField())),
              ('ratings', orm.DictField(orm.StringField(), orm.DocumentField(Rating))),
              ('regions', orm.ListField(orm.StringField())),
              ('merged', orm.BooleanField(required=True, default=False)),
              ('merge_parent', orm.ObjectIDField()),
              ('merge_children', orm.ListField(orm.ObjectIDField()))
              ]

    def post_init(self):
        # initialize merge_children to contain id if it does not already
        if not self.merge_children:
            self.merge_children = [self.id]

    @classmethod
    def create_with_default_values(cls, name, region):
        return cls(id=ObjectId(),
                   name=name,
                   aliases=[name.lower()],
                   ratings={},
                   regions=[region])


class Tournament(orm.Document):
    fields = [('id', orm.ObjectIDField(required=True, load_from=MONGO_ID_SELECTOR,
                                       dump_to=MONGO_ID_SELECTOR)),
              ('name', orm.StringField(required=True)),
              ('type', orm.StringField(
                  required=True,
                  validators=[orm.validate_choices(SOURCE_TYPE_CHOICES)])),
              ('date', orm.DateTimeField()),
              ('regions', orm.ListField(orm.StringField())),
              ('url', orm.StringField()),
              ('raw', orm.StringField()),
              ('matches', orm.ListField(orm.DocumentField(Match))),
              ('players', orm.ListField(orm.ObjectIDField())),
              ('orig_ids', orm.ListField(orm.ObjectIDField()))]

    def replace_player(self, player_to_remove=None, player_to_add=None):
        # TODO edge cases with this
        # TODO the player being added cannot play himself in any match
        if player_to_remove is None or player_to_add is None:
            raise TypeError(
                "player_to_remove and player_to_add cannot be None!")

        player_to_remove_id = player_to_remove.id
        player_to_add_id = player_to_add.id

        if player_to_remove_id not in self.players:
            print "Player with id %s is not in this tournament. Ignoring." % player_to_remove.id
            return

        self.players.remove(player_to_remove_id)
        self.players.append(player_to_add_id)

        for match in self.matches:
            if match.winner == player_to_remove_id:
                match.winner = player_to_add_id

            if match.loser == player_to_remove_id:
                match.loser = player_to_add_id

    @classmethod
    def from_pending_tournament(cls, pending_tournament):
        # takes a real alias to id map instead of a list of objects
        def _get_player_id_from_map_or_throw(alias_to_id_map, alias):
            if alias in alias_to_id_map:
                return alias_to_id_map[alias]
            else:
                raise ValueError('Alias %s has no ID in map\n: %s' %
                                 (alias, alias_to_id_map))

        alias_to_id_map = dict([(entry.player_alias, entry.player_id)
                                for entry in pending_tournament.alias_to_id_map
                                if entry.player_id is not None])

        # we need to convert pending tournament players/matches to player IDs
        print pending_tournament.players, pending_tournament.matches
        players = [_get_player_id_from_map_or_throw(
            alias_to_id_map, p) for p in pending_tournament.players]
        matches = []
        for am in pending_tournament.matches:
            m = Match(
                winner=_get_player_id_from_map_or_throw(
                    alias_to_id_map, am.winner),
                loser=_get_player_id_from_map_or_throw(
                    alias_to_id_map, am.loser)
            )
            matches.append(m)
        return cls(
            id=pending_tournament.id,
            name=pending_tournament.name,
            type=pending_tournament.type,
            date=pending_tournament.date,
            regions=pending_tournament.regions,
            url=pending_tournament.url,
            raw=pending_tournament.raw,
            matches=matches,
            players=players,
            orig_ids=players)


class PendingTournament(orm.Document):
    fields = [('id', orm.ObjectIDField(required=True, load_from=MONGO_ID_SELECTOR,
                                       dump_to=MONGO_ID_SELECTOR)),
              ('name', orm.StringField(required=True)),
              ('type', orm.StringField(required=True)),
              ('date', orm.DateTimeField()),
              ('regions', orm.ListField(orm.StringField())),
              ('url', orm.StringField()),
              ('raw', orm.StringField()),
              ('matches', orm.ListField(orm.DocumentField(AliasMatch))),
              ('players', orm.ListField(orm.StringField())),
              ('alias_to_id_map', orm.ListField(orm.DocumentField(AliasMapping)))]

    def set_alias_id_mapping(self, alias, id):
        if self.alias_to_id_map is None:
            self.alias_to_id_map = []

        for mapping in self.alias_to_id_map:
            if mapping.player_alias == alias:
                mapping.player_alias = alias
                mapping.player_id = id
                return

        # if we've gotten out here, we couldn't find an existing match, so add
        # a new element
        self.alias_to_id_map.append(AliasMapping(
            player_alias=alias,
            player_id=id
        ))

    def delete_alias_id_mapping(self, alias):
        if self.alias_to_id_map is None:
            self.alias_to_id_map = []

        for mapping in self.alias_to_id_map:
            if mapping.player_alias == alias:
                self.alias_to_id_map.remove(mapping)
                return mapping

    @classmethod
    def from_scraper(cls, type, scraper, region_id):
        regions = [region_id]
        return cls(
            id=ObjectId(),
            name=scraper.get_name(),
            type=type,
            date=scraper.get_date(),
            regions=regions,
            url=scraper.get_url(),
            raw=scraper.get_raw(),
            players=scraper.get_players(),
            matches=scraper.get_matches())


class Ranking(orm.Document):
    fields = [('id', orm.ObjectIDField(required=True, load_from=MONGO_ID_SELECTOR,
                                       dump_to=MONGO_ID_SELECTOR)),
              ('region', orm.StringField(required=True)),
              ('tournaments', orm.ListField(orm.ObjectIDField())),
              ('time', orm.DateTimeField()),
              ('ranking', orm.ListField(orm.DocumentField(RankingEntry)))]


class Region(orm.Document):
    fields = [('id', orm.StringField(required=True, load_from=MONGO_ID_SELECTOR,
                                     dump_to=MONGO_ID_SELECTOR)),
              ('display_name', orm.StringField(required=True))]


class User(orm.Document):
    fields = [('id', orm.StringField(required=True, load_from=MONGO_ID_SELECTOR,
                                     dump_to=MONGO_ID_SELECTOR)),
              ('username', orm.StringField(required=True)),
              ('salt', orm.StringField(required=True)),
              ('hashed_password', orm.StringField(required=True)),
              ('admin_regions', orm.ListField(orm.StringField()))]


class Merge(orm.Document):
    fields = [('id', orm.ObjectIDField(required=True, load_from=MONGO_ID_SELECTOR,
                                       dump_to=MONGO_ID_SELECTOR)),
              ('requester_user_id', orm.StringField(required=True)),
              ('source_player_obj_id', orm.ObjectIDField(required=True)),
              ('target_player_obj_id', orm.ObjectIDField(required=True)),
              ('time', orm.DateTimeField())]


class Session(orm.Document):
    fields = [('session_id', orm.StringField(required=True)),
              ('user_id', orm.StringField(required=True))]
