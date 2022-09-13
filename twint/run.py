import sys, os, datetime
from asyncio import get_event_loop, TimeoutError, ensure_future, new_event_loop, set_event_loop

from . import datelock, feed, get, output, verbose, storage
from .token import TokenExpiryException
from . import token
from .storage import db
from .feed import NoMoreTweetsException

import logging as logme

import time

bearer = 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs' \
         '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA'


class Twint:
    def __init__(self, config):
        logme.debug(f'{__name__}:Twint:__init__')
        if config.Resume is not None and (config.TwitterSearch or config.Followers or config.Following):
            logme.debug(f'{__name__}:Twint:__init__:Resume')
            self.init = self.get_resume(config.Resume)
        else:
            self.init = -1

        config.deleted = []
        self.feed: list = [-1]
        self.count = 0
        self.user_agent = ""
        self.config = config
        self.config.Bearer_token = bearer
        # TODO might have to make some adjustments for it to work with multi-treading
        # USAGE : to get a new guest token simply do `self.token.refresh()`
        self.token = token.Token(config)
        self.token.refresh()
        self.conn = db.Conn(config.Database)
        self.d = datelock.Set(self.config.Until, self.config.Since)
        verbose.Elastic(config.Elasticsearch)

        if self.config.Store_object:
            logme.debug(f'{__name__}:Twint:__init__:clean_follow_list')
            output._clean_follow_list()

        if self.config.Pandas_clean:
            logme.debug(f'{__name__}:Twint:__init__:pandas_clean')
            storage.panda.clean()

    def get_resume(self, resumeFile):
        if not os.path.exists(resumeFile):
            return '-1'
        with open(resumeFile, 'r') as rFile:
            return rFile.readlines()[-1].strip('\n')

    async def Feed(self):
        logme.debug(f'{__name__}:Twint:Feed')
        consecutive_errors_count = 0
        while True:
            # this will receive a JSON string, parse it into a `dict` and do the required stuff
            try:
                response = await get.RequestUrl(self.config, self.init)
            except TokenExpiryException as e:
                logme.debug(f'{__name__}Twint:Feed:{str(e)}')
                self.token.refresh()
                response = await get.RequestUrl(self.config, self.init)

            if self.config.Debug:
                print(response, file=open("twint-last-request.log", "w", encoding="utf-8"))

            self.feed = []
            try:
                if self.config.Favorites:
                    self.feed, self.init = feed.MobileFav(response)
                    if len(self.feed) == 0 and len(self.init) == 0:
                        favorite_err_cnt = 0
                        while (len(self.feed) == 0 or len(self.init) == 0) and favorite_err_cnt < 5:
                            self.user_agent = await get.RandomUserAgent(wa=False)
                            response = await get.RequestUrl(self.config, self.init,
                                                            headers=[("User-Agent", self.user_agent)])
                            self.feed, self.init = feed.MobileFav(response)
                            favorite_err_cnt += 1
                            time.sleep(1)
                        if favorite_err_cnt == 5:
                            print("Favorite page could not be fetched")
                    if not self.count % 40:
                        time.sleep(5)
                elif self.config.Followers or self.config.Following:
                    self.feed, self.init = feed.Follow(response)
                    if not self.count % 40:
                        time.sleep(5)
                elif self.config.Profile or self.config.TwitterSearch:
                    try:
                        self.feed, self.init = feed.parse_tweets(self.config, response)
                    except NoMoreTweetsException as e:
                        logme.debug(f'{__name__}:Twint:Feed:{str(e)}')
                        print(f'[!] {str(e)} Scraping will stop now.')
                        print(f'found {len(self.config.deleted)} deleted tweets in this search.')
                        break
                break
            except TimeoutError as e:
                if self.config.Proxy_host.lower() == "tor":
                    print("[?] Timed out, changing Tor identity...")
                    if self.config.Tor_control_password is None:
                        logme.critical(f'{__name__}:Twint:Feed:tor-password')
                        sys.stderr.write("Error: config.Tor_control_password must be set for proxy auto-rotation!\r\n")
                        sys.stderr.write(
                            "Info: What is it? See https://stem.torproject.org/faq.html#can-i-interact-with-tors"
                            "-controller-interface-directly\r\n")
                        break
                    else:
                        get.ForceNewTorIdentity(self.config)
                        continue
                else:
                    logme.critical(f'{__name__}:Twint:Feed:{str(e)}')
                    print(e)
                    break
            except Exception as e:
                if self.config.Profile or self.config.Favorites:
                    print("[!] Twitter does not return more data, scrape stops here.")
                    break

                logme.critical(f'{__name__}:Twint:Feed:noData{str(e)}')
                # Sometimes Twitter says there is no data. But it's a lie.
                # raise
                consecutive_errors_count += 1
                if consecutive_errors_count < self.config.Retries_count:
                    # skip to the next iteration if wait time does not satisfy limit constraints
                    delay = round(consecutive_errors_count ** self.config.Backoff_exponent, 1)

                    # if the delay is less than users set min wait time then replace delay
                    if self.config.Min_wait_time > delay:
                        delay = self.config.Min_wait_time

                    sys.stderr.write(f'sleeping for {delay} secs\n')
                    time.sleep(delay)
                    self.user_agent = await get.RandomUserAgent(wa=True)
                    continue
                logme.critical(f'{__name__}:Twint:Feed:Tweets_known_error:{str(e)}')
                sys.stderr.write(f"{str(e)} [x] run.Feed")
                sys.stderr.write(
                    "[!] if you get this error but you know for sure that more tweets exist, please open an issue and "
                    "we will investigate it!")
                break
        if self.config.Resume:
            print(self.init, file=open(self.config.Resume, "a", encoding="utf-8"))

    async def follow(self):
        await self.Feed()
        if self.config.User_full:
            logme.debug(f'{__name__}:Twint:follow:userFull')
            self.count += await get.Multi(self.feed, self.config, self.conn)
        else:
            logme.debug(f'{__name__}:Twint:follow:notUserFull')
            for user in self.feed:
                self.count += 1
                username = user.find("a")["name"]
                await output.Username(username, self.config, self.conn)

    async def favorite(self):
        logme.debug(f'{__name__}:Twint:favorite')
        await self.Feed()
        favorited_tweets_list = []
        for tweet in self.feed:
            tweet_dict = {}
            self.count += 1
            try:
                tweet_dict['data-item-id'] = tweet.find("div", {"class": "tweet-text"})['data-id']
                t_url = tweet.find("span", {"class": "metadata"}).find("a")["href"]
                tweet_dict['data-conversation-id'] = t_url.split('?')[0].split('/')[-1]
                tweet_dict['username'] = tweet.find("div", {"class": "username"}).text.replace('\n', '').replace(' ',
                                                                                                                 '')
                tweet_dict['tweet'] = tweet.find("div", {"class": "tweet-text"}).find("div", {"class": "dir-ltr"}).text
                date_str = tweet.find("td", {"class": "timestamp"}).find("a").text
                # test_dates = ["1m", "2h", "Jun 21, 2019", "Mar 12", "28 Jun 19"]
                # date_str = test_dates[3]
                if len(date_str) <= 3 and date_str[-1] in ["m", "h"]:  # 25m 1h
                    dateu = str(datetime.date.today())
                elif ',' in date_str:  # Aug 21, 2019
                    sp = date_str.replace(',', '').split(' ')
                    date_str_formatted = f'{sp[1]} {sp[0]} {sp[2]}'
                    dateu = datetime.datetime.strptime(date_str_formatted, "%d %b %Y").strftime("%Y-%m-%d")
                elif len(date_str.split(' ')) == 3:  # 28 Jun 19
                    sp = date_str.split(' ')
                    if len(sp[2]) == 2:
                        sp[2] = f'20{sp[2]}'
                    date_str_formatted = f'{sp[0]} {sp[1]} {sp[2]}'
                    dateu = datetime.datetime.strptime(date_str_formatted, "%d %b %Y").strftime("%Y-%m-%d")
                else:  # Aug 21
                    sp = date_str.split(' ')
                    date_str_formatted = f'{sp[1]} {sp[0]} {str(datetime.date.today().year)}'
                    dateu = datetime.datetime.strptime(date_str_formatted, "%d %b %Y").strftime("%Y-%m-%d")
                tweet_dict['date'] = dateu
                favorited_tweets_list.append(tweet_dict)

            except Exception as e:
                logme.critical(f'{__name__}:Twint:favorite:favorite_field_lack')
                print("shit: ", date_str, " ", e)

        try:
            self.config.favorited_tweets_list += favorited_tweets_list
        except AttributeError:
            self.config.favorited_tweets_list = favorited_tweets_list

    async def profile(self):
        await self.Feed()
        logme.debug(f'{__name__}:Twint:profile')
        for tweet in self.feed:
            self.count += 1
            await output.Tweets(tweet, self.config, self.conn)

    async def tweets(self):
        await self.Feed()
        # TODO : need to take care of this later
        if self.config.Location:
            logme.debug(f'{__name__}:Twint:tweets:location')
            self.count += await get.Multi(self.feed, self.config, self.conn)
        else:
            logme.debug(f'{__name__}:Twint:tweets:notLocation')
            for tweet in self.feed:
                self.count += 1
                await output.Tweets(tweet, self.config, self.conn)

    async def main(self, callback=None):

        task = ensure_future(self.run())  # Might be changed to create_task in 3.7+.

        if callback:
            task.add_done_callback(callback)

        await task

    async def run(self):
        if self.config.TwitterSearch:
            self.user_agent = await get.RandomUserAgent(wa=True)
        else:
            self.user_agent = await get.RandomUserAgent()

        if self.config.User_id is not None and self.config.Username is None:
            logme.debug(f'{__name__}:Twint:main:user_id')
            self.config.Username = await get.Username(self.config.User_id, self.config.Bearer_token,
                                                      self.config.Guest_token)

        if self.config.Username is not None and self.config.User_id is None:
            logme.debug(f'{__name__}:Twint:main:username')

            self.config.User_id = await get.User(self.config.Username, self.config, self.conn, True)
            if self.config.User_id is None:
                raise ValueError(
                    f"Cannot find twitter account with name = {self.config.Username}"
                )


        # TODO : will need to modify it to work with the new endpoints
        if self.config.TwitterSearch and self.config.Since and self.config.Until:
            logme.debug(f'{__name__}:Twint:main:search+since+until')
            while self.d.since < self.d.until:
                self.config.Since = datetime.datetime.strftime(self.d.since, "%Y-%m-%d %H:%M:%S")
                self.config.Until = datetime.datetime.strftime(self.d.until, "%Y-%m-%d %H:%M:%S")
                if len(self.feed) > 0:
                    await self.tweets()
                else:
                    logme.debug(f'{__name__}:Twint:main:gettingNewTweets')
                    break

                if get.Limit(self.config.Limit, self.count):
                    break
        elif self.config.Lookup:
            await self.Lookup()
        else:
            logme.debug(f'{__name__}:Twint:main:not-search+since+until')
            while True:
                if len(self.feed) > 0:
                    if self.config.Followers or self.config.Following:
                        logme.debug(f'{__name__}:Twint:main:follow')
                        await self.follow()
                    elif self.config.Favorites:
                        logme.debug(f'{__name__}:Twint:main:favorites')
                        await self.favorite()
                    elif self.config.Profile:
                        logme.debug(f'{__name__}:Twint:main:profile')
                        await self.profile()
                    elif self.config.TwitterSearch:
                        logme.debug(f'{__name__}:Twint:main:twitter-search')
                        await self.tweets()
                else:
                    logme.debug(f'{__name__}:Twint:main:no-more-tweets')
                    break

                # logging.info("[<] " + str(datetime.now()) + ':: run+Twint+main+CallingGetLimit2')
                if get.Limit(self.config.Limit, self.count):
                    logme.debug(f'{__name__}:Twint:main:reachedLimit')
                    break

        if self.config.Count:
            verbose.Count(self.count, self.config)

    async def Lookup(self):
        logme.debug(f'{__name__}:Twint:Lookup')

        try:
            if self.config.User_id is not None and self.config.Username is None:
                logme.debug(f'{__name__}:Twint:Lookup:user_id')
                self.config.Username = await get.Username(self.config.User_id, self.config.Bearer_token,
                                                          self.config.Guest_token)
            await get.User(self.config.Username, self.config, db.Conn(self.config.Database))

        except Exception as e:
            logme.exception(f'{__name__}:Twint:Lookup:Unexpected exception occurred.')
            raise


def run(config, callback=None):
    logme.debug(f'{__name__}:run')
    try:
        get_event_loop()
    except RuntimeError as e:
        if "no current event loop" in str(e):
            set_event_loop(new_event_loop())
        else:
            logme.exception(
                f'{__name__}:run:Unexpected exception while handling an expected RuntimeError.'
            )

            raise
    except Exception as e:
        logme.exception(
            f'{__name__}:run:Unexpected exception occurred while attempting to get or create a new event loop.'
        )

        raise

    get_event_loop().run_until_complete(Twint(config).main(callback))


def Favorites(config):
    logme.debug(f'{__name__}:Favorites')
    config.Favorites = True
    config.Following = False
    config.Followers = False
    config.Profile = False
    config.TwitterSearch = False
    run(config)
    if config.Pandas_au:
        storage.panda._autoget("tweet")


def Followers(config):
    logme.debug(f'{__name__}:Followers')
    config.Followers = True
    config.Following = False
    config.Profile = False
    config.Favorites = False
    config.TwitterSearch = False
    run(config)
    if config.Pandas_au:
        storage.panda._autoget("followers")
        if config.User_full:
            storage.panda._autoget("user")
    if config.Pandas_clean and not config.Store_object:
        # storage.panda.clean()
        output._clean_follow_list()


def Following(config):
    logme.debug(f'{__name__}:Following')
    config.Following = True
    config.Followers = False
    config.Profile = False
    config.Favorites = False
    config.TwitterSearch = False
    run(config)
    if config.Pandas_au:
        storage.panda._autoget("following")
        if config.User_full:
            storage.panda._autoget("user")
    if config.Pandas_clean and not config.Store_object:
        # storage.panda.clean()
        output._clean_follow_list()


def Lookup(config):
    logme.debug(f'{__name__}:Lookup')
    config.Profile = False
    config.Lookup = True
    config.Favorites = False
    config.FOllowing = False
    config.Followers = False
    config.TwitterSearch = False
    run(config)
    if config.Pandas_au:
        storage.panda._autoget("user")


def Profile(config):
    logme.debug(f'{__name__}:Profile')
    config.Profile = True
    config.Favorites = False
    config.Following = False
    config.Followers = False
    config.TwitterSearch = False
    run(config)
    if config.Pandas_au:
        storage.panda._autoget("tweet")


def Search(config, callback=None):
    logme.debug(f'{__name__}:Search')
    config.TwitterSearch = True
    config.Favorites = False
    config.Following = False
    config.Followers = False
    config.Profile = False
    run(config, callback)
    if config.Pandas_au:
        storage.panda._autoget("tweet")
