event list: curl 'https://spectate-web.mrgreen.se/spectate/eventsrequest/getEventsDigest/boxing' \
  -H 'accept: */*' \
  -H 'accept-language: en-GB,en;q=0.9,sv;q=0.8' \
  -H 'cache-control: no-cache' \
  -b 'anon_hash=c94e2283da416020931e139fb433c981; odds_format=DECIMAL; 888Cookie=isftd%3Dfalse%26isHybrid%3Dfalse%26isreal%3Dfalse%26lang%3Dsv%26queryCountry%3Dswe%26queryState%3Dab; spectate_client_ver=2.145; bbsess=rqzg-7YqPIi5Nzf1bARnQMGqxhk; lang=swe; spectate_session=f5f0d309-1cf3-49f8-93f2-4ae38117496f%3Aanon; 888TestData=%7B%22orig-lp%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2Fsport%2Ffotboll%2Fspanien%2Fspanish-la-liga-primera%2Flevante-vs-elche-e-6973626%2F%22%2C%22currentvisittype%22%3A%22Unknown%22%2C%22strategy%22%3A%22UnknownStrategy%22%2C%22strategysource%22%3A%22previousvisit%22%2C%22referrer%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2F%22%7D; 888TestDataLocal=%7B%22datecreated%22%3A%222026-01-21T10%3A43%3A38.551Z%22%2C%22expiredat%22%3A%22Wed%2C%2028%20Jan%202026%2010%3A43%3A00%20GMT%22%2C%22datemodified%22%3A%222026-01-21T14%3A49%3A27.016Z%22%2C%22modifiedcounter%22%3A%222%22%2C%22trackingId%22%3A%22nepeYvvUP29yguF9uQ7K7hgGNWLPWNGP655BQMlcqka5xfyZ4Vnkvg%3D%3D%22%7D; 888Attribution=1' \
  -H 'origin: https://www.mrgreen.se' \
  -H 'pragma: no-cache' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.mrgreen.se/' \
  -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'

  json respone:
  {
    "sport_slug": "boxing",
    "sport_id": 354,
    "live": 0,
    "starting_soon": 0,
    "today": 0,
    "tomorrow": 0,
    "upcoming": {
        "2026-01-24": 1,
        "2026-01-25": 3,
        "2026-01-31": 6,
        "2026-02-01": 6,
        "2026-02-07": 2,
        "2026-02-21": 2,
        "2026-02-23": 1,
        "2026-03-01": 1,
        "2026-03-14": 2,
        "2026-03-28": 5
    },
    "display_day_tabs": false
}

odds exists here:

curl 'https://spectate-web.mrgreen.se/spectate/sportsbook-req/getUpcomingEvents/boxing/upcoming' \
  -H 'accept: */*' \
  -H 'accept-language: en-GB,en;q=0.9,sv;q=0.8' \
  -H 'cache-control: no-cache' \
  -H 'content-type: multipart/form-data; boundary=----WebKitFormBoundaryQ5RAQxk9ozbkr9H6' \
  -b 'anon_hash=c94e2283da416020931e139fb433c981; odds_format=DECIMAL; 888Cookie=isftd%3Dfalse%26isHybrid%3Dfalse%26isreal%3Dfalse%26lang%3Dsv%26queryCountry%3Dswe%26queryState%3Dab; spectate_client_ver=2.145; bbsess=rqzg-7YqPIi5Nzf1bARnQMGqxhk; lang=swe; spectate_session=f5f0d309-1cf3-49f8-93f2-4ae38117496f%3Aanon; 888TestData=%7B%22orig-lp%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2Fsport%2Ffotboll%2Fspanien%2Fspanish-la-liga-primera%2Flevante-vs-elche-e-6973626%2F%22%2C%22currentvisittype%22%3A%22Unknown%22%2C%22strategy%22%3A%22UnknownStrategy%22%2C%22strategysource%22%3A%22previousvisit%22%2C%22referrer%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2F%22%7D; 888TestDataLocal=%7B%22datecreated%22%3A%222026-01-21T10%3A43%3A38.551Z%22%2C%22expiredat%22%3A%22Wed%2C%2028%20Jan%202026%2010%3A43%3A00%20GMT%22%2C%22datemodified%22%3A%222026-01-21T14%3A49%3A27.016Z%22%2C%22modifiedcounter%22%3A%222%22%2C%22trackingId%22%3A%22nepeYvvUP29yguF9uQ7K7hgGNWLPWNGP655BQMlcqka5xfyZ4Vnkvg%3D%3D%22%7D; 888Attribution=1' \
  -H 'origin: https://www.mrgreen.se' \
  -H 'pragma: no-cache' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.mrgreen.se/' \
  -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36' \
  --data-raw $'------WebKitFormBoundaryQ5RAQxk9ozbkr9H6--\r\n'


json:[{
    "selection_pointers": [
        {
            "event_id": 6990486,
            "market_id": 8207,
            "selection_id": 16613924115
        },
        {
            "event_id": 6990486,
            "market_id": 8207,
            "selection_id": 16613924116
        },
        {
            "event_id": 6990486,
            "market_id": 8207,
            "selection_id": 16613924117
        },
        {
            "event_id": 6926166,
            "market_id": 8207,
            "selection_id": 16601855829
        },
        {
            "event_id": 6926166,
            "market_id": 8207,
            "selection_id": 16601855830
        },
        {
            "event_id": 6926166,
            "market_id": 8207,
            "selection_id": 16601855831
        },
        {
            "event_id": 6990487,
            "market_id": 8207,
            "selection_id": 16613924120
        },
        {
            "event_id": 6990487,
            "market_id": 8207,
            "selection_id": 16613924121
        },
        {
            "event_id": 6990487,
            "market_id": 8207,
            "selection_id": 16613924122
        },
        {
            "event_id": 6990488,
            "market_id": 8207,
            "selection_id": 16613924132
        },
        {
            "event_id": 6990488,
            "market_id": 8207,
            "selection_id": 16613924133
        },
        {
            "event_id": 6990488,
            "market_id": 8207,
            "selection_id": 16613924134
        },
        {
            "event_id": 7013877,
            "market_id": 8207,
            "selection_id": 16618510139
        },
        {
            "event_id": 7013877,
            "market_id": 8207,
            "selection_id": 16618510140
        },
        {
            "event_id": 7013877,
            "market_id": 8207,
            "selection_id": 16618510141
        },
        {
            "event_id": 7013876,
            "market_id": 8207,
            "selection_id": 16618510134
        },
        {
            "event_id": 7013876,
            "market_id": 8207,
            "selection_id": 16618510135
        },
        {
            "event_id": 7013876,
            "market_id": 8207,
            "selection_id": 16618510136
        },
        {
            "event_id": 7013878,
            "market_id": 8207,
            "selection_id": 16618510144
        },
        {
            "event_id": 7013878,
            "market_id": 8207,
            "selection_id": 16618510145
        },
        {
            "event_id": 7013878,
            "market_id": 8207,
            "selection_id": 16618510146
        },
        {
            "event_id": 7013879,
            "market_id": 8207,
            "selection_id": 16618510149
        },
        {
            "event_id": 7013879,
            "market_id": 8207,
            "selection_id": 16618510150
        },
        {
            "event_id": 7013879,
            "market_id": 8207,
            "selection_id": 16618510151
        },
        {
            "event_id": 6922495,
            "market_id": 8207,
            "selection_id": 16601294102
        },
        {
            "event_id": 6922495,
            "market_id": 8207,
            "selection_id": 16601294103
        },
        {
            "event_id": 6922495,
            "market_id": 8207,
            "selection_id": 16601294104
        },
        {
            "event_id": 6922496,
            "market_id": 8207,
            "selection_id": 16601294155
        },
        {
            "event_id": 6922496,
            "market_id": 8207,
            "selection_id": 16601294156
        },
        {
            "event_id": 6922496,
            "market_id": 8207,
            "selection_id": 16601294157
        },
        {
            "event_id": 7013880,
            "market_id": 8207,
            "selection_id": 16618510154
        },
        {
            "event_id": 7013880,
            "market_id": 8207,
            "selection_id": 16618510155
        },
        {
            "event_id": 7013880,
            "market_id": 8207,
            "selection_id": 16618510156
        },
        {
            "event_id": 6956822,
            "market_id": 8207,
            "selection_id": 16607568621
        },
        {
            "event_id": 6956822,
            "market_id": 8207,
            "selection_id": 16607568622
        },
        {
            "event_id": 6956822,
            "market_id": 8207,
            "selection_id": 16607568623
        },
        {
            "event_id": 6956823,
            "market_id": 8207,
            "selection_id": 16607568634
        },
        {
            "event_id": 6956823,
            "market_id": 8207,
            "selection_id": 16607568635
        },
        {
            "event_id": 6956823,
            "market_id": 8207,
            "selection_id": 16607568636
        },
        {
            "event_id": 6956824,
            "market_id": 8207,
            "selection_id": 16607568639
        },
        {
            "event_id": 6956824,
            "market_id": 8207,
            "selection_id": 16607568640
        },
        {
            "event_id": 6956824,
            "market_id": 8207,
            "selection_id": 16607568641
        },
        {
            "event_id": 6922497,
            "market_id": 8207,
            "selection_id": 16601294208
        },
        {
            "event_id": 6922497,
            "market_id": 8207,
            "selection_id": 16601294209
        },
        {
            "event_id": 6922497,
            "market_id": 8207,
            "selection_id": 16601294210
        },
        {
            "event_id": 6922498,
            "market_id": 8207,
            "selection_id": 16601294363
        },
        {
            "event_id": 6922498,
            "market_id": 8207,
            "selection_id": 16601294364
        },
        {
            "event_id": 6922498,
            "market_id": 8207,
            "selection_id": 16601294365
        },
        {
            "event_id": 6956827,
            "market_id": 8207,
            "selection_id": 16607568660
        },
        {
            "event_id": 6956827,
            "market_id": 8207,
            "selection_id": 16607568661
        },
        {
            "event_id": 6956827,
            "market_id": 8207,
            "selection_id": 16607568662
        },
        {
            "event_id": 6956825,
            "market_id": 8207,
            "selection_id": 16607568650
        },
        {
            "event_id": 6956825,
            "market_id": 8207,
            "selection_id": 16607568651
        },
        {
            "event_id": 6956825,
            "market_id": 8207,
            "selection_id": 16607568652
        },
        {
            "event_id": 6956826,
            "market_id": 8207,
            "selection_id": 16607568655
        },
        {
            "event_id": 6956826,
            "market_id": 8207,
            "selection_id": 16607568656
        },
        {
            "event_id": 6956826,
            "market_id": 8207,
            "selection_id": 16607568657
        },
        {
            "event_id": 6956828,
            "market_id": 8207,
            "selection_id": 16607568665
        },
        {
            "event_id": 6956828,
            "market_id": 8207,
            "selection_id": 16607568666
        },
        {
            "event_id": 6956828,
            "market_id": 8207,
            "selection_id": 16607568667
        },
        {
            "event_id": 6956829,
            "market_id": 8207,
            "selection_id": 16607568672
        },
        {
            "event_id": 6956829,
            "market_id": 8207,
            "selection_id": 16607568673
        },
        {
            "event_id": 6956829,
            "market_id": 8207,
            "selection_id": 16607568674
        },
        {
            "event_id": 6956830,
            "market_id": 8207,
            "selection_id": 16607568683
        },
        {
            "event_id": 6956830,
            "market_id": 8207,
            "selection_id": 16607568684
        },
        {
            "event_id": 6956830,
            "market_id": 8207,
            "selection_id": 16607568685
        },
        {
            "event_id": 6956831,
            "market_id": 8207,
            "selection_id": 16607568688
        },
        {
            "event_id": 6956831,
            "market_id": 8207,
            "selection_id": 16607568689
        },
        {
            "event_id": 6956831,
            "market_id": 8207,
            "selection_id": 16607568690
        },
        {
            "event_id": 6956832,
            "market_id": 8207,
            "selection_id": 16607568693
        },
        {
            "event_id": 6956832,
            "market_id": 8207,
            "selection_id": 16607568694
        },
        {
            "event_id": 6956832,
            "market_id": 8207,
            "selection_id": 16607568695
        },
        {
            "event_id": 7013881,
            "market_id": 8207,
            "selection_id": 16618510159
        },
        {
            "event_id": 7013881,
            "market_id": 8207,
            "selection_id": 16618510160
        },
        {
            "event_id": 7013881,
            "market_id": 8207,
            "selection_id": 16618510161
        },
        {
            "event_id": 7013882,
            "market_id": 8207,
            "selection_id": 16618510164
        },
        {
            "event_id": 7013882,
            "market_id": 8207,
            "selection_id": 16618510165
        },
        {
            "event_id": 7013882,
            "market_id": 8207,
            "selection_id": 16618510166
        },
        {
            "event_id": 7013883,
            "market_id": 8207,
            "selection_id": 16618510169
        },
        {
            "event_id": 7013883,
            "market_id": 8207,
            "selection_id": 16618510170
        },
        {
            "event_id": 7013883,
            "market_id": 8207,
            "selection_id": 16618510171
        },
        {
            "event_id": 6719446,
            "market_id": 8207,
            "selection_id": 16565834614
        },
        {
            "event_id": 6719446,
            "market_id": 8207,
            "selection_id": 16565834615
        },
        {
            "event_id": 6719446,
            "market_id": 8207,
            "selection_id": 16565834616
        },
        {
            "event_id": 7013884,
            "market_id": 8207,
            "selection_id": 16618510176
        },
        {
            "event_id": 7013884,
            "market_id": 8207,
            "selection_id": 16618510177
        },
        {
            "event_id": 7013884,
            "market_id": 8207,
            "selection_id": 16618510178
        }
    ],
    "events": {
        "6922496": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922496,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "bakhram-murtazaliev-v-josh-kelly",
            "category_name": "Boxing",
            "event_l10n_slug": "bakhram-murtazaliev-v-josh-kelly",
            "racing_name": null,
            "name": "Bakhram Murtazaliev v Josh Kelly",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16601294155": {
                            "fraction_price": "3/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16601294155,
                            "name": "Bakhram Murtazaliev",
                            "decimal_price": "1.300",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16601294156": {
                            "fraction_price": "11/4",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16601294156,
                            "name": "Josh Kelly",
                            "decimal_price": "3.750",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16601294157": {
                            "fraction_price": "18/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16601294157,
                            "name": "Draw",
                            "decimal_price": "19.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "38138": {
                    "slug": "bakhram-murtazaliev",
                    "order": 1,
                    "is_home_team": true,
                    "id": 38138,
                    "name": "Bakhram Murtazaliev"
                },
                "10054": {
                    "slug": "josh-kelly",
                    "order": 2,
                    "is_home_team": false,
                    "id": 10054,
                    "name": "Josh Kelly"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "bakhram-murtazaliev-v-josh-kelly",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6922497": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922497,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T03:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "teofimo-lopez-v-shakur-stevenson",
            "category_name": "Boxing",
            "event_l10n_slug": "teofimo-lopez-v-shakur-stevenson",
            "racing_name": null,
            "name": "Teofimo Lopez v Shakur Stevenson",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16601294208": {
                            "fraction_price": "5/2",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16601294208,
                            "name": "Teofimo Lopez",
                            "decimal_price": "3.500",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16601294209": {
                            "fraction_price": "12/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16601294209,
                            "name": "Draw",
                            "decimal_price": "13.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16601294210": {
                            "fraction_price": "1/3",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16601294210,
                            "name": "Shakur Stevenson",
                            "decimal_price": "1.333",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "60001": {
                    "slug": "teofimo-lopez",
                    "order": 1,
                    "is_home_team": true,
                    "id": 60001,
                    "name": "Teofimo Lopez"
                },
                "10047": {
                    "slug": "shakur-stevenson",
                    "order": 2,
                    "is_home_team": false,
                    "id": 10047,
                    "name": "Shakur Stevenson"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "teofimo-lopez-v-shakur-stevenson",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6922498": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922498,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T04:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "xander-zayas-v-abass-baraou",
            "category_name": "Boxing",
            "event_l10n_slug": "xander-zayas-v-abass-baraou",
            "racing_name": null,
            "name": "Xander Zayas v Abass Baraou",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16601294363": {
                            "fraction_price": "14/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16601294363,
                            "name": "Draw",
                            "decimal_price": "15.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16601294364": {
                            "fraction_price": "1/3",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16601294364,
                            "name": "Xander Zayas",
                            "decimal_price": "1.333",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16601294365": {
                            "fraction_price": "27/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16601294365,
                            "name": "Abass Baraou",
                            "decimal_price": "3.700",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "286432": {
                    "slug": "abass-baraou",
                    "order": 2,
                    "is_home_team": false,
                    "id": 286432,
                    "name": "Abass Baraou"
                },
                "145322": {
                    "slug": "xander-zayas",
                    "order": 1,
                    "is_home_team": true,
                    "id": 145322,
                    "name": "Xander Zayas"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "xander-zayas-v-abass-baraou",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956822": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956822,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T01:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "bruce-carrington-v-carlos-castro",
            "category_name": "Boxing",
            "event_l10n_slug": "bruce-carrington-v-carlos-castro",
            "racing_name": null,
            "name": "Bruce Carrington v Carlos Castro",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568621": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568621,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568622": {
                            "fraction_price": "2/15",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568622,
                            "name": "Bruce Carrington",
                            "decimal_price": "1.133",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16607568623": {
                            "fraction_price": "28/5",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568623,
                            "name": "Carlos Castro",
                            "decimal_price": "6.600",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "206608": {
                    "slug": "bruce-carrington",
                    "order": 1,
                    "is_home_team": true,
                    "id": 206608,
                    "name": "Bruce Carrington"
                },
                "128437": {
                    "slug": "carlos-castro",
                    "order": 2,
                    "is_home_team": false,
                    "id": 128437,
                    "name": "Carlos Castro"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "bruce-carrington-v-carlos-castro",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6990487": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6990487,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-25T05:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-25",
            "active": true,
            "sport_slug": "boxing",
            "slug": "israil-madrimov-v-luis-david-salazar",
            "category_name": "Boxing",
            "event_l10n_slug": "israil-madrimov-v-luis-david-salazar",
            "racing_name": null,
            "name": "Israil Madrimov v Luis David Salazar",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16613924120": {
                            "fraction_price": "20/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16613924120,
                            "name": "Draw",
                            "decimal_price": "21.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16613924121": {
                            "fraction_price": "1/25",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16613924121,
                            "name": "Israil Madrimov",
                            "decimal_price": "1.040",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16613924122": {
                            "fraction_price": "11/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16613924122,
                            "name": "Luis David Salazar",
                            "decimal_price": "12.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "486213": {
                    "slug": "luis-david-salazar",
                    "order": 2,
                    "is_home_team": false,
                    "id": 486213,
                    "name": "Luis David Salazar"
                },
                "29822": {
                    "slug": "israil-madrimov",
                    "order": 1,
                    "is_home_team": true,
                    "id": 29822,
                    "name": "Israil Madrimov"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "israil-madrimov-v-luis-david-salazar",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6990488": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6990488,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-25T05:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-25",
            "active": true,
            "sport_slug": "boxing",
            "slug": "khalil-coe-v-jesse-hart",
            "category_name": "Boxing",
            "event_l10n_slug": "khalil-coe-v-jesse-hart",
            "racing_name": null,
            "name": "Khalil Coe v Jesse Hart",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16613924132": {
                            "fraction_price": "20/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16613924132,
                            "name": "Draw",
                            "decimal_price": "21.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16613924133": {
                            "fraction_price": "2/15",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16613924133,
                            "name": "Khalil Coe",
                            "decimal_price": "1.133",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16613924134": {
                            "fraction_price": "5/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16613924134,
                            "name": "Jesse Hart",
                            "decimal_price": "6.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "216336": {
                    "slug": "khalil-coe",
                    "order": 1,
                    "is_home_team": true,
                    "id": 216336,
                    "name": "Khalil Coe"
                },
                "486214": {
                    "slug": "jesse-hart",
                    "order": 2,
                    "is_home_team": false,
                    "id": 486214,
                    "name": "Jesse Hart"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "khalil-coe-v-jesse-hart",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6990486": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6990486,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-24T05:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-24",
            "active": true,
            "sport_slug": "boxing",
            "slug": "callum-walsh-v-carlos-ocampo",
            "category_name": "Boxing",
            "event_l10n_slug": "callum-walsh-v-carlos-ocampo",
            "racing_name": null,
            "name": "Callum Walsh v Carlos Ocampo",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16613924115": {
                            "fraction_price": "2/15",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16613924115,
                            "name": "Callum Walsh",
                            "decimal_price": "1.133",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16613924116": {
                            "fraction_price": "22/5",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16613924116,
                            "name": "Carlos Ocampo",
                            "decimal_price": "5.400",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16613924117": {
                            "fraction_price": "14/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16613924117,
                            "name": "Draw",
                            "decimal_price": "15.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "453741": {
                    "slug": "callum-walsh",
                    "order": 1,
                    "is_home_team": true,
                    "id": 453741,
                    "name": "Callum Walsh"
                },
                "209718": {
                    "slug": "carlos-ocampo",
                    "order": 2,
                    "is_home_team": false,
                    "id": 209718,
                    "name": "Carlos Ocampo"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "callum-walsh-v-carlos-ocampo",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956823": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956823,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T02:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "keyshawn-davis-v-jamaine-ortiz",
            "category_name": "Boxing",
            "event_l10n_slug": "keyshawn-davis-v-jamaine-ortiz",
            "racing_name": null,
            "name": "Keyshawn Davis v Jamaine Ortiz",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568634": {
                            "fraction_price": "2/9",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568634,
                            "name": "Keyshawn Davis",
                            "decimal_price": "1.222",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16607568635": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568635,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568636": {
                            "fraction_price": "37/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568636,
                            "name": "Jamaine Ortiz",
                            "decimal_price": "4.700",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "123229": {
                    "slug": "keyshawn-davis",
                    "order": 1,
                    "is_home_team": true,
                    "id": 123229,
                    "name": "Keyshawn Davis"
                },
                "102158": {
                    "slug": "jamaine-ortiz",
                    "order": 2,
                    "is_home_team": false,
                    "id": 102158,
                    "name": "Jamaine Ortiz"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "keyshawn-davis-v-jamaine-ortiz",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956824": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956824,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T02:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "carlos-adames-v-austin-williams",
            "category_name": "Boxing",
            "event_l10n_slug": "carlos-adames-v-austin-williams",
            "racing_name": null,
            "name": "Carlos Adames v Austin Williams",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568640": {
                            "fraction_price": "4/15",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568640,
                            "name": "Carlos Adames",
                            "decimal_price": "1.266",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16607568641": {
                            "fraction_price": "31/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568641,
                            "name": "Austin Williams",
                            "decimal_price": "4.100",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16607568639": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568639,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "154666": {
                    "slug": "carlos-adames",
                    "order": 1,
                    "is_home_team": true,
                    "id": 154666,
                    "name": "Carlos Adames"
                },
                "75916": {
                    "slug": "austin-williams",
                    "order": 2,
                    "is_home_team": false,
                    "id": 75916,
                    "name": "Austin Williams"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "carlos-adames-v-austin-williams",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956825": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956825,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-07T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-07",
            "active": true,
            "sport_slug": "boxing",
            "slug": "nick-ball-v-brandon-figueroa",
            "category_name": "Boxing",
            "event_l10n_slug": "nick-ball-v-brandon-figueroa",
            "racing_name": null,
            "name": "Nick Ball v Brandon Figueroa",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568650": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568650,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568651": {
                            "fraction_price": "14/5",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568651,
                            "name": "Brandon Figueroa",
                            "decimal_price": "3.800",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16607568652": {
                            "fraction_price": "3/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568652,
                            "name": "Nick Ball",
                            "decimal_price": "1.300",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "28048": {
                    "slug": "nick-ball",
                    "order": 1,
                    "is_home_team": true,
                    "id": 28048,
                    "name": "Nick Ball"
                },
                "38152": {
                    "slug": "brandon-figueroa",
                    "order": 2,
                    "is_home_team": false,
                    "id": 38152,
                    "name": "Brandon Figueroa"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "nick-ball-v-brandon-figueroa",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956826": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956826,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-21T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-21",
            "active": true,
            "sport_slug": "boxing",
            "slug": "ishmael-davis-v-bilal-fawaz",
            "category_name": "Boxing",
            "event_l10n_slug": "ishmael-davis-v-bilal-fawaz",
            "racing_name": null,
            "name": "Ishmael Davis v Bilal Fawaz",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568656": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568656,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568657": {
                            "fraction_price": "21/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568657,
                            "name": "Bilal Fawaz",
                            "decimal_price": "3.100",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16607568655": {
                            "fraction_price": "2/5",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568655,
                            "name": "Ishmael Davis",
                            "decimal_price": "1.400",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "274128": {
                    "slug": "ishmael-davis",
                    "order": 1,
                    "is_home_team": true,
                    "id": 274128,
                    "name": "Ishmael Davis"
                },
                "388532": {
                    "slug": "bilal-fawaz",
                    "order": 2,
                    "is_home_team": false,
                    "id": 388532,
                    "name": "Bilal Fawaz"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "ishmael-davis-v-bilal-fawaz",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956827": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956827,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-07T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-07",
            "active": true,
            "sport_slug": "boxing",
            "slug": "andrew-cain-v-alejandro-jair-gonzalez",
            "category_name": "Boxing",
            "event_l10n_slug": "andrew-cain-v-alejandro-jair-gonzalez",
            "racing_name": null,
            "name": "Andrew Cain v Alejandro Jair Gonzalez",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568660": {
                            "fraction_price": "1/9",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568660,
                            "name": "Andrew Cain",
                            "decimal_price": "1.111",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16607568661": {
                            "fraction_price": "20/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568661,
                            "name": "Draw",
                            "decimal_price": "21.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568662": {
                            "fraction_price": "6/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568662,
                            "name": "Alejandro Jair Gonzalez",
                            "decimal_price": "7.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "28278": {
                    "slug": "andrew-cain",
                    "order": 1,
                    "is_home_team": true,
                    "id": 28278,
                    "name": "Andrew Cain"
                },
                "484895": {
                    "slug": "alejandro-jair-gonzalez",
                    "order": 2,
                    "is_home_team": false,
                    "id": 484895,
                    "name": "Alejandro Jair Gonzalez"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "andrew-cain-v-alejandro-jair-gonzalez",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956828": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956828,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-21T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-21",
            "active": true,
            "sport_slug": "boxing",
            "slug": "leigh-wood-v-josh-warrington",
            "category_name": "Boxing",
            "event_l10n_slug": "leigh-wood-v-josh-warrington",
            "racing_name": null,
            "name": "Leigh Wood v Josh Warrington",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568665": {
                            "fraction_price": "8/13",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568665,
                            "name": "Leigh Wood",
                            "decimal_price": "1.615",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16607568666": {
                            "fraction_price": "29/20",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568666,
                            "name": "Josh Warrington",
                            "decimal_price": "2.450",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16607568667": {
                            "fraction_price": "14/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568667,
                            "name": "Draw",
                            "decimal_price": "15.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "79733": {
                    "slug": "leigh-wood",
                    "order": 1,
                    "is_home_team": true,
                    "id": 79733,
                    "name": "Leigh Wood"
                },
                "79735": {
                    "slug": "josh-warrington",
                    "order": 2,
                    "is_home_team": false,
                    "id": 79735,
                    "name": "Josh Warrington"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "leigh-wood-v-josh-warrington",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956829": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956829,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-23T04:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-23",
            "active": true,
            "sport_slug": "boxing",
            "slug": "claressa-shields-v-franchon-crews-dezurn",
            "category_name": "Boxing",
            "event_l10n_slug": "claressa-shields-v-franchon-crews-dezurn",
            "racing_name": null,
            "name": "Claressa Shields v Franchon Crews Dezurn",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568672": {
                            "fraction_price": "22/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568672,
                            "name": "Draw",
                            "decimal_price": "23.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568673": {
                            "fraction_price": "10/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568673,
                            "name": "Franchon Crews Dezurn",
                            "decimal_price": "11.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16607568674": {
                            "fraction_price": "1/20",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568674,
                            "name": "Claressa Shields",
                            "decimal_price": "1.050",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "90706": {
                    "slug": "claressa-shields",
                    "order": 1,
                    "is_home_team": true,
                    "id": 90706,
                    "name": "Claressa Shields"
                },
                "177091": {
                    "slug": "franchon-crews-dezurn",
                    "order": 2,
                    "is_home_team": false,
                    "id": 177091,
                    "name": "Franchon Crews Dezurn"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "claressa-shields-v-franchon-crews-dezurn",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956830": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956830,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-01T05:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "emanuel-navarrete-v-eduardo-nunez",
            "category_name": "Boxing",
            "event_l10n_slug": "emanuel-navarrete-v-eduardo-nunez",
            "racing_name": null,
            "name": "Emanuel Navarrete v Eduardo Nunez",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568683": {
                            "fraction_price": "19/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568683,
                            "name": "Emanuel Navarrete",
                            "decimal_price": "2.900",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16607568684": {
                            "fraction_price": "18/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568684,
                            "name": "Draw",
                            "decimal_price": "19.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568685": {
                            "fraction_price": "4/9",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568685,
                            "name": "Eduardo Nunez",
                            "decimal_price": "1.444",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "450892": {
                    "slug": "eduardo-nunez",
                    "order": 2,
                    "is_home_team": false,
                    "id": 450892,
                    "name": "Eduardo Nunez"
                },
                "59148": {
                    "slug": "emanuel-navarrete",
                    "order": 1,
                    "is_home_team": true,
                    "id": 59148,
                    "name": "Emanuel Navarrete"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "emanuel-navarrete-v-eduardo-nunez",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956831": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956831,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-14T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-14",
            "active": true,
            "sport_slug": "boxing",
            "slug": "pierce-oleary-v-mark-chamberlain",
            "category_name": "Boxing",
            "event_l10n_slug": "pierce-oleary-v-mark-chamberlain",
            "racing_name": null,
            "name": "Pierce O'Leary v Mark Chamberlain",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568688": {
                            "fraction_price": "3/4",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568688,
                            "name": "Mark Chamberlain",
                            "decimal_price": "1.750",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16607568689": {
                            "fraction_price": "18/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568689,
                            "name": "Draw",
                            "decimal_price": "19.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568690": {
                            "fraction_price": "23/20",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568690,
                            "name": "Pierce O'Leary",
                            "decimal_price": "2.150",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "170618": {
                    "slug": "mark-chamberlain",
                    "order": 2,
                    "is_home_team": false,
                    "id": 170618,
                    "name": "Mark Chamberlain"
                },
                "29524": {
                    "slug": "pierce-o-leary",
                    "order": 1,
                    "is_home_team": true,
                    "id": 29524,
                    "name": "Pierce O'Leary"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "pierce-oleary-v-mark-chamberlain",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6956832": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956832,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-14T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-14",
            "active": true,
            "sport_slug": "boxing",
            "slug": "james-dickens-v-anthony-cacace",
            "category_name": "Boxing",
            "event_l10n_slug": "james-dickens-v-anthony-cacace",
            "racing_name": null,
            "name": "James Dickens v Anthony Cacace",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16607568693": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16607568693,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16607568694": {
                            "fraction_price": "2/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16607568694,
                            "name": "James Dickens",
                            "decimal_price": "3.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16607568695": {
                            "fraction_price": "4/9",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16607568695,
                            "name": "Anthony Cacace",
                            "decimal_price": "1.444",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "88228": {
                    "slug": "anthony-cacace",
                    "order": 2,
                    "is_home_team": false,
                    "id": 88228,
                    "name": "Anthony Cacace"
                },
                "69109": {
                    "slug": "james-dickens",
                    "order": 1,
                    "is_home_team": true,
                    "id": 69109,
                    "name": "James Dickens"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "james-dickens-v-anthony-cacace",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6926166": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6926166,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-25T03:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-25",
            "active": true,
            "sport_slug": "boxing",
            "slug": "raymond-muratalla-v-andy-cruz",
            "category_name": "Boxing",
            "event_l10n_slug": "raymond-muratalla-v-andy-cruz",
            "racing_name": null,
            "name": "Raymond Muratalla v Andy Cruz",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16601855829": {
                            "fraction_price": "4/11",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16601855829,
                            "name": "Andy Cruz",
                            "decimal_price": "1.364",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16601855830": {
                            "fraction_price": "23/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16601855830,
                            "name": "Raymond Muratalla",
                            "decimal_price": "3.300",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16601855831": {
                            "fraction_price": "12/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16601855831,
                            "name": "Draw",
                            "decimal_price": "13.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "152106": {
                    "slug": "raymond-muratalla",
                    "order": 1,
                    "is_home_team": true,
                    "id": 152106,
                    "name": "Raymond Muratalla"
                },
                "123238": {
                    "slug": "andy-cruz",
                    "order": 2,
                    "is_home_team": false,
                    "id": 123238,
                    "name": "Andy Cruz"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "raymond-muratalla-v-andy-cruz",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6719446": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6719446,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-28T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-28",
            "active": true,
            "sport_slug": "boxing",
            "slug": "willy-hutchinson-v-ezra-taylor",
            "category_name": "Boxing",
            "event_l10n_slug": "willy-hutchinson-v-ezra-taylor",
            "racing_name": null,
            "name": "Willy Hutchinson v Ezra Taylor",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16565834616": {
                            "fraction_price": "29/20",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16565834616,
                            "name": "Ezra Taylor",
                            "decimal_price": "2.450",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16565834614": {
                            "fraction_price": "8/13",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16565834614,
                            "name": "Willy Hutchinson",
                            "decimal_price": "1.615",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16565834615": {
                            "fraction_price": "14/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16565834615,
                            "name": "Draw",
                            "decimal_price": "15.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "31293": {
                    "slug": "willy-hutchinson",
                    "order": 1,
                    "is_home_team": true,
                    "id": 31293,
                    "name": "Willy Hutchinson"
                },
                "232445": {
                    "slug": "ezra-taylor",
                    "order": 2,
                    "is_home_team": false,
                    "id": 232445,
                    "name": "Ezra Taylor"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "willy-hutchinson-v-ezra-taylor",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013876": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013876,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "elif-nur-turhan-v-taylah-gentzen",
            "category_name": "Boxing",
            "event_l10n_slug": "elif-nur-turhan-v-taylah-gentzen",
            "racing_name": null,
            "name": "Elif Nur Turhan v Taylah Gentzen",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510136": {
                            "fraction_price": "33/4",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510136,
                            "name": "Taylah Gentzen",
                            "decimal_price": "9.250",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16618510134": {
                            "fraction_price": "1/16",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510134,
                            "name": "Elif Nur Turhan",
                            "decimal_price": "1.062",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16618510135": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510135,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "383817": {
                    "slug": "taylah-gentzen",
                    "order": 2,
                    "is_home_team": false,
                    "id": 383817,
                    "name": "Taylah Gentzen"
                },
                "469598": {
                    "slug": "elif-nur-turhan",
                    "order": 1,
                    "is_home_team": true,
                    "id": 469598,
                    "name": "Elif Nur Turhan"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "elif-nur-turhan-v-taylah-gentzen",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013877": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013877,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "gradus-kraus-v-boris-crighton",
            "category_name": "Boxing",
            "event_l10n_slug": "gradus-kraus-v-boris-crighton",
            "racing_name": null,
            "name": "Gradus Kraus v Boris Crighton",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510139": {
                            "fraction_price": "9/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510139,
                            "name": "Boris Crighton",
                            "decimal_price": "10.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16618510140": {
                            "fraction_price": "18/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510140,
                            "name": "Draw",
                            "decimal_price": "19.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16618510141": {
                            "fraction_price": "1/16",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510141,
                            "name": "Gradus Kraus",
                            "decimal_price": "1.062",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "248059": {
                    "slug": "boris-crighton",
                    "order": 2,
                    "is_home_team": false,
                    "id": 248059,
                    "name": "Boris Crighton"
                },
                "486989": {
                    "slug": "gradus-kraus",
                    "order": 1,
                    "is_home_team": true,
                    "id": 486989,
                    "name": "Gradus Kraus"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "gradus-kraus-v-boris-crighton",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013878": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013878,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "josh-padley-v-jaouad-belmehdi",
            "category_name": "Boxing",
            "event_l10n_slug": "josh-padley-v-jaouad-belmehdi",
            "racing_name": null,
            "name": "Josh Padley v Jaouad Belmehdi",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510144": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510144,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16618510145": {
                            "fraction_price": "3/17",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510145,
                            "name": "Josh Padley",
                            "decimal_price": "1.176",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16618510146": {
                            "fraction_price": "22/5",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510146,
                            "name": "Jaouad Belmehdi",
                            "decimal_price": "5.400",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "325384": {
                    "slug": "josh-padley",
                    "order": 1,
                    "is_home_team": true,
                    "id": 325384,
                    "name": "Josh Padley"
                },
                "212994": {
                    "slug": "jaouad-belmehdi",
                    "order": 2,
                    "is_home_team": false,
                    "id": 212994,
                    "name": "Jaouad Belmehdi"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "josh-padley-v-jaouad-belmehdi",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013879": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013879,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "francesca-hennessy-v-ellie-bouttell",
            "category_name": "Boxing",
            "event_l10n_slug": "francesca-hennessy-v-ellie-bouttell",
            "racing_name": null,
            "name": "Francesca Hennessy v Ellie Bouttell",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510149": {
                            "fraction_price": "14/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510149,
                            "name": "Draw",
                            "decimal_price": "15.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16618510150": {
                            "fraction_price": "5/2",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510150,
                            "name": "Ellie Bouttell",
                            "decimal_price": "3.500",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16618510151": {
                            "fraction_price": "4/11",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510151,
                            "name": "Francesca Hennessy",
                            "decimal_price": "1.364",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "479873": {
                    "slug": "ellie-bouttell",
                    "order": 2,
                    "is_home_team": false,
                    "id": 479873,
                    "name": "Ellie Bouttell"
                },
                "266994": {
                    "slug": "francesca-hennessy",
                    "order": 1,
                    "is_home_team": true,
                    "id": 266994,
                    "name": "Francesca Hennessy"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "francesca-hennessy-v-ellie-bouttell",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013880": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013880,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T23:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "jacob-bank-v-william-scull",
            "category_name": "Boxing",
            "event_l10n_slug": "jacob-bank-v-william-scull",
            "racing_name": null,
            "name": "Jacob Bank v William Scull",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510154": {
                            "fraction_price": "6/4",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510154,
                            "name": "Jacob Bank",
                            "decimal_price": "2.500",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16618510155": {
                            "fraction_price": "4/7",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510155,
                            "name": "William Scull",
                            "decimal_price": "1.571",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16618510156": {
                            "fraction_price": "18/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510156,
                            "name": "Draw",
                            "decimal_price": "19.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "308857": {
                    "slug": "william-scull",
                    "order": 2,
                    "is_home_team": false,
                    "id": 308857,
                    "name": "William Scull"
                },
                "486990": {
                    "slug": "jacob-bank",
                    "order": 1,
                    "is_home_team": true,
                    "id": 486990,
                    "name": "Jacob Bank"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "jacob-bank-v-william-scull",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013881": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013881,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-28T20:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-28",
            "active": true,
            "sport_slug": "boxing",
            "slug": "shakiel-thompson-v-brad-pauls",
            "category_name": "Boxing",
            "event_l10n_slug": "shakiel-thompson-v-brad-pauls",
            "racing_name": null,
            "name": "Shakiel Thompson v Brad Pauls",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510160": {
                            "fraction_price": "29/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510160,
                            "name": "Brad Pauls",
                            "decimal_price": "3.900",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16618510161": {
                            "fraction_price": "14/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510161,
                            "name": "Draw",
                            "decimal_price": "15.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16618510159": {
                            "fraction_price": "3/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510159,
                            "name": "Shakiel Thompson",
                            "decimal_price": "1.300",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "225361": {
                    "slug": "brad-pauls",
                    "order": 2,
                    "is_home_team": false,
                    "id": 225361,
                    "name": "Brad Pauls"
                },
                "188683": {
                    "slug": "shakiel-thompson",
                    "order": 1,
                    "is_home_team": true,
                    "id": 188683,
                    "name": "Shakiel Thompson"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "shakiel-thompson-v-brad-pauls",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013882": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013882,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-28T20:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-28",
            "active": true,
            "sport_slug": "boxing",
            "slug": "nathan-heaney-v-gerome-warburton",
            "category_name": "Boxing",
            "event_l10n_slug": "nathan-heaney-v-gerome-warburton",
            "racing_name": null,
            "name": "Nathan Heaney v Gerome Warburton",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510164": {
                            "fraction_price": "8/13",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510164,
                            "name": "Nathan Heaney",
                            "decimal_price": "1.615",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16618510165": {
                            "fraction_price": "16/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510165,
                            "name": "Draw",
                            "decimal_price": "17.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16618510166": {
                            "fraction_price": "11/8",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510166,
                            "name": "Gerome Warburton",
                            "decimal_price": "2.375",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "88728": {
                    "slug": "nathan-heaney",
                    "order": 1,
                    "is_home_team": true,
                    "id": 88728,
                    "name": "Nathan Heaney"
                },
                "321745": {
                    "slug": "gerome-warburton",
                    "order": 2,
                    "is_home_team": false,
                    "id": 321745,
                    "name": "Gerome Warburton"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "nathan-heaney-v-gerome-warburton",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013883": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013883,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-28T21:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-28",
            "active": true,
            "sport_slug": "boxing",
            "slug": "liam-davies-v-zak-miller",
            "category_name": "Boxing",
            "event_l10n_slug": "liam-davies-v-zak-miller",
            "racing_name": null,
            "name": "Liam Davies v Zak Miller",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510169": {
                            "fraction_price": "19/10",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510169,
                            "name": "Zak Miller",
                            "decimal_price": "2.900",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16618510170": {
                            "fraction_price": "4/9",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510170,
                            "name": "Liam Davies",
                            "decimal_price": "1.444",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16618510171": {
                            "fraction_price": "18/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510171,
                            "name": "Draw",
                            "decimal_price": "19.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "275369": {
                    "slug": "zak-miller",
                    "order": 2,
                    "is_home_team": false,
                    "id": 275369,
                    "name": "Zak Miller"
                },
                "70583": {
                    "slug": "liam-davies",
                    "order": 1,
                    "is_home_team": true,
                    "id": 70583,
                    "name": "Liam Davies"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "liam-davies-v-zak-miller",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "7013884": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013884,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-03-28T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-03-28",
            "active": true,
            "sport_slug": "boxing",
            "slug": "moses-itauma-v-jermaine-franklin",
            "category_name": "Boxing",
            "event_l10n_slug": "moses-itauma-v-jermaine-franklin",
            "racing_name": null,
            "name": "Moses Itauma v Jermaine Franklin",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16618510176": {
                            "fraction_price": "20/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16618510176,
                            "name": "Draw",
                            "decimal_price": "21.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        },
                        "16618510177": {
                            "fraction_price": "1/16",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16618510177,
                            "name": "Moses Itauma",
                            "decimal_price": "1.062",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16618510178": {
                            "fraction_price": "33/4",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16618510178,
                            "name": "Jermaine Franklin",
                            "decimal_price": "9.250",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "225059": {
                    "slug": "moses-itauma",
                    "order": 1,
                    "is_home_team": true,
                    "id": 225059,
                    "name": "Moses Itauma"
                },
                "217516": {
                    "slug": "jermaine-franklin",
                    "order": 2,
                    "is_home_team": false,
                    "id": 217516,
                    "name": "Jermaine Franklin"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "moses-itauma-v-jermaine-franklin",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        },
        "6922495": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922495,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "adam-azim-v-gustavo-daniel-lemos",
            "category_name": "Boxing",
            "event_l10n_slug": "adam-azim-v-gustavo-daniel-lemos",
            "racing_name": null,
            "name": "Adam Azim v Gustavo Daniel Lemos",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "tradable": true,
                    "name": "Fightodds",
                    "betable": true,
                    "selections": {
                        "16601294104": {
                            "fraction_price": "27/5",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 3,
                            "id": 16601294104,
                            "name": "Gustavo Daniel Lemos",
                            "decimal_price": "6.400",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "2"
                        },
                        "16601294102": {
                            "fraction_price": "2/15",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 1,
                            "id": 16601294102,
                            "name": "Adam Azim",
                            "decimal_price": "1.133",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "1"
                        },
                        "16601294103": {
                            "fraction_price": "18/1",
                            "betable": true,
                            "boosted_odds": false,
                            "active": true,
                            "selection_type_id": 2,
                            "id": 16601294103,
                            "name": "Draw",
                            "decimal_price": "19.000",
                            "ranking_place": null,
                            "tradable": true,
                            "special_odds_value": null,
                            "sp_only": false,
                            "outcome": "UNSETTLED",
                            "type": "X"
                        }
                    },
                    "active": true,
                    "is_extra_time": false,
                    "selection_schema": 1,
                    "id": 8207,
                    "default_line_selections": []
                }
            },
            "tradable": false,
            "competitors": {
                "302324": {
                    "slug": "gustavo-daniel-lemos",
                    "order": 2,
                    "is_home_team": false,
                    "id": 302324,
                    "name": "Gustavo Daniel Lemos"
                },
                "152172": {
                    "slug": "adam-azim",
                    "order": 1,
                    "is_home_team": true,
                    "id": 152172,
                    "name": "Adam Azim"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "category_slug_i18n": "boxing",
            "event_slug_i18n": "adam-azim-v-gustavo-daniel-lemos",
            "tournament_slug_i18n": "boxing-bouts",
            "sport_slug_i18n": "boxning",
            "usesThreeMarketView": false,
            "isUsFormat": false,
            "is_streaming_available": false,
            "tournament_logos": null
        }
    },
    "event_order": [
        {
            "event_id": 6990486,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6926166,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6990487,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6990488,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013877,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013876,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013878,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013879,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6922495,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6922496,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013880,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956822,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956823,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956824,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6922497,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6922498,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956827,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956825,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956826,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956828,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956829,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956830,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956831,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6956832,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013881,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013882,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013883,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 6719446,
            "grouped_market_ids": [],
            "market_id": 8207
        },
        {
            "event_id": 7013884,
            "grouped_market_ids": [],
            "market_id": 8207
        }
    ],
    "match_request_limit": "+365 days"
}]

all sport slugs for spectate:[
    curl 'https://spectate-web.mrgreen.se/spectate/load/state' \
  -H 'accept: */*' \
  -H 'accept-language: en-GB,en;q=0.9,sv;q=0.8' \
  -H 'cache-control: no-cache' \
  -H 'content-type: multipart/form-data; boundary=----WebKitFormBoundarysAbgL1TD4XYVodBA' \
  -b 'anon_hash=c94e2283da416020931e139fb433c981; odds_format=DECIMAL; 888Cookie=isftd%3Dfalse%26isHybrid%3Dfalse%26isreal%3Dfalse%26lang%3Dsv%26queryCountry%3Dswe%26queryState%3Dab; spectate_client_ver=2.145; bbsess=rqzg-7YqPIi5Nzf1bARnQMGqxhk; lang=swe; spectate_session=f5f0d309-1cf3-49f8-93f2-4ae38117496f%3Aanon; 888TestData=%7B%22orig-lp%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2Fsport%2Ffotboll%2Fspanien%2Fspanish-la-liga-primera%2Flevante-vs-elche-e-6973626%2F%22%2C%22currentvisittype%22%3A%22Unknown%22%2C%22strategy%22%3A%22UnknownStrategy%22%2C%22strategysource%22%3A%22previousvisit%22%2C%22referrer%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2F%22%7D; 888TestDataLocal=%7B%22datecreated%22%3A%222026-01-21T10%3A43%3A38.551Z%22%2C%22expiredat%22%3A%22Wed%2C%2028%20Jan%202026%2010%3A43%3A00%20GMT%22%2C%22datemodified%22%3A%222026-01-21T14%3A49%3A27.016Z%22%2C%22modifiedcounter%22%3A%222%22%2C%22trackingId%22%3A%22nepeYvvUP29yguF9uQ7K7hgGNWLPWNGP655BQMlcqka5xfyZ4Vnkvg%3D%3D%22%7D; 888Attribution=1' \
  -H 'origin: https://www.mrgreen.se' \
  -H 'pragma: no-cache' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.mrgreen.se/' \
  -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36' \
  --data-raw $'------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="currency_code"\r\n\r\nSEK\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="language"\r\n\r\nswe\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="sub_brand_id"\r\n\r\n153\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="brand_id"\r\n\r\n92\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="marketing_brand_id"\r\n\r\n3\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="regulation_type_id"\r\n\r\n15\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="timezone"\r\n\r\n-1\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="browsing_country_code"\r\n\r\nSWE\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="product_package_id"\r\n\r\n112\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="user_mode"\r\n\r\nAnonymous\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="spectate_timezone"\r\n\r\nEurope/Stockholm\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="device"\r\n\r\nPC\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="referrer"\r\n\r\nhttps://www.mrgreen.se/sport/boxning/\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="region"\r\n\r\nab\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA\r\nContent-Disposition: form-data; name="theme_mode"\r\n\r\n2\r\n------WebKitFormBoundarysAbgL1TD4XYVodBA--\r\n'


  respons:
  {
    "clientState": {
        "wrapper:didReceivePRDCMessage": false,
        "onboardingPage:enabled": false,
        "babyslip:notification-hide-delay": "5000",
        "betslip:animation-enabled": "1",
        "betslip:max-selections": "25",
        "betslip:keypad-animation-enabled": "1",
        "betfeed:page:status": 1,
        "betfeed:widget:status": 1,
        "websockets:config": {
            "cashout": {
                "url": "wss://spectate-cashout.888sport.se",
                "ping": {
                    "interval": 7000,
                    "pongWait": 1000,
                    "maxLostPongs": 2
                },
                "reconnectionDelay": {
                    "min": 3000,
                    "max": 15000,
                    "growFactor": 1.25,
                    "jitterMax": 1000
                }
            },
            "general": {
                "url": "wss://spectate-ws-general.888sport.se",
                "ping": {
                    "interval": 7000,
                    "pongWait": 1000,
                    "maxLostPongs": 2
                },
                "reconnectionDelay": {
                    "min": 3000,
                    "max": 15000,
                    "growFactor": 1.25,
                    "jitterMax": 1000
                }
            },
            "sb_live_data": {
                "url": "wss://spectate-ws-live-data.888sport.se",
                "ping": {
                    "interval": 7000,
                    "pongWait": 1000,
                    "maxLostPongs": 2
                },
                "reconnectionDelay": {
                    "min": 3000,
                    "max": 15000,
                    "growFactor": 1.25,
                    "jitterMax": 1000
                }
            }
        },
        "recommend:selections:delay": 2,
        "recommend:global:selection:status": 1,
        "recommend:betplacement:selection:status": 1,
        "recommend:betreceipt:selection:status": 1,
        "recommend:numberOfBets:selection:status": 1,
        "recommend:numberOfbBetsThreshold:selection": 2,
        "layout:breakpoint:px": 900,
        "sports": {
            "horse-racing": {
                "id": "1",
                "name": "Horse Racing"
            },
            "football": {
                "id": "2",
                "name": "Football"
            },
            "golf": {
                "id": "3",
                "name": "Golf"
            },
            "tennis": {
                "id": "4",
                "name": "Tennis"
            },
            "american-football": {
                "id": "6",
                "name": "American Football"
            },
            "greyhound-racing": {
                "id": "7",
                "name": "Greyhound Racing"
            },
            "trotting": {
                "id": "200",
                "name": "Trotting"
            },
            "basketball": {
                "id": "229",
                "name": "Basketball"
            },
            "darts": {
                "id": "238",
                "name": "Darts"
            },
            "boxing": {
                "id": "354",
                "name": "Boxing"
            },
            "snooker": {
                "id": "360",
                "name": "Snooker"
            },
            "ice-hockey": {
                "id": "362",
                "name": "Ice Hockey"
            },
            "baseball": {
                "id": "363",
                "name": "Baseball"
            },
            "athletics": {
                "id": "404",
                "name": "Winter Olympics"
            },
            "australian-rules": {
                "id": "405",
                "name": "Australian Rules"
            },
            "badminton": {
                "id": "406",
                "name": "Badminton"
            },
            "beach-volleyball": {
                "id": "411",
                "name": "Beach Volleyball"
            },
            "cricket": {
                "id": "416",
                "name": "Cricket"
            },
            "cycling": {
                "id": "418",
                "name": "Cycling"
            },
            "diving": {
                "id": "420",
                "name": "Diving"
            },
            "equestrian": {
                "id": "421",
                "name": "Equestrian"
            },
            "gymnastics": {
                "id": "429",
                "name": "Gymnastics"
            },
            "handball": {
                "id": "430",
                "name": "Handball"
            },
            "hockey": {
                "id": "431",
                "name": "Hockey"
            },
            "mma": {
                "id": "438",
                "name": "MMA"
            },
            "motor-racing": {
                "id": "439",
                "name": "Motor Racing"
            },
            "netball": {
                "id": "440",
                "name": "Netball"
            },
            "novelty-bets": {
                "id": "441",
                "name": "Novelty Bets"
            },
            "politics": {
                "id": "443",
                "name": "Politics"
            },
            "pool": {
                "id": "444",
                "name": "Pool"
            },
            "rowing": {
                "id": "445",
                "name": "Rowing"
            },
            "rugby-league": {
                "id": "446",
                "name": "Rugby League"
            },
            "rugby-union": {
                "id": "447",
                "name": "Rugby Union"
            },
            "surfing": {
                "id": "454",
                "name": "Surfing"
            },
            "swimming": {
                "id": "455",
                "name": "Swimming"
            },
            "table-tennis": {
                "id": "456",
                "name": "Table Tennis"
            },
            "triathlon": {
                "id": "458",
                "name": "Triathlon"
            },
            "volleyball": {
                "id": "460",
                "name": "Volleyball"
            },
            "waterpolo": {
                "id": "461",
                "name": "Waterpolo"
            },
            "winter-sports": {
                "id": "462",
                "name": "Ski Jumping"
            },
            "wrestling": {
                "id": "463",
                "name": "Wrestling"
            },
            "gaa-football": {
                "id": "607",
                "name": "GAA Football"
            },
            "gaa-hurling": {
                "id": "608",
                "name": "GAA Hurling"
            },
            "virtual-sports": {
                "id": "900",
                "name": "Virtual Sports"
            },
            "field-hockey": {
                "id": "2776",
                "name": "Field Hockey"
            },
            "esports": {
                "id": "8229",
                "name": "eSports"
            },
            "odds-boost": {
                "id": "8337",
                "name": "Odds Boost"
            },
            "gaelic-sports": {
                "id": "11306",
                "name": "Gaelic Sports"
            }
        },
        "lazyload:enabled": true,
        "lazyload:placeholders": {
            "SportsWidget:football": 1116,
            "InplayWidget": 469,
            "CarouselIcons": 95,
            "SportsWidget:ice-hockey": 986,
            "SportsWidget:tennis": 464,
            "PopularAccasWidget": 332,
            "RacingWidget:meetingPage;horse-racing": 58,
            "SportsWidget:handball": 292
        },
        "metrics_collection:component_height": {
            "enabled": true,
            "debounce_duration": 5000
        },
        "newrelic:logging": true,
        "banners:enabled": true,
        "market_switcher:sports": [
            "football",
            "tennis"
        ],
        "race_switcher:enabled": "1",
        "racingwidget:jitter": "10",
        "category_flags": {
            "sports": [
                "basketball",
                "football",
                "handball",
                "ice-hockey",
                "volleyball"
            ]
        },
        "visualisations": {
            "enabled": true
        },
        "statscenter": {
            "iframeUrl": "https://s5.sir.sportradar.com/",
            "iframeScriptUrl": "https://s5.sir.sportradar.com/iframescript.js",
            "enabled": true
        },
        "competitions_only_sport_slugs": [
            "athletics",
            "politics",
            "cycling",
            "novelty-bets",
            "surfing"
        ],
        "grouping_only_sport_slugs": [
            "motor-racing"
        ],
        "non_standard_sports": [
            "athletics",
            "cycling",
            "golf",
            "greyhound-racing",
            "horse-racing",
            "motor-racing",
            "novelty-bets",
            "politics",
            "surfing",
            "swimming",
            "trotting",
            "winter-sports"
        ],
        "sports_without_countdown": [
            "greyhound-racing",
            "horse-racing",
            "trotting"
        ],
        "all_markets_group_open_markets": 5,
        "search_enabled": true,
        "haptic_feedback:enabled": true,
        "brand_statscenter": "mrgreen",
        "brand_betbuilder": "mrgreen",
        "marketing_brand_betbuilder_url_hash": "#betbuilder",
        "marketing_brand_bg_visualisation": "mrgreen",
        "heartbeat": {
            "enabled": false,
            "interval": 30
        },
        "hide_inplay_selections": [
            "motor-racing"
        ],
        "banners:placeholderId": {
            "generic": "C84FC198-DE46-465B-8F60-64E6E20FE067",
            "football": "0E8F4449-54C2-4412-970D-47D2EEB67E1D"
        },
        "spotlight:sports": [
            "american-football",
            "football",
            "horse-racing",
            "basketball",
            "darts",
            "tennis",
            "snooker"
        ],
        "articles:enabled": true,
        "preplay:scoreboard_background:override": {},
        "ttls": {
            "marketsDescriptions": 1800,
            "raceSwitcher": 180,
            "marketSwitcherOptions": 86400,
            "marketSwitcherSelections": 3,
            "homepageWidgets": 1800,
            "carouselIcons": 3600,
            "popularWidget": 3,
            "oddsboostWidget": 30,
            "moreGames": 3,
            "sportsWidget": 3,
            "racingMenu": 300,
            "sportsPages": 3
        },
        "client_inactivity": {
            "inactivityTimeout": 300,
            "inactivityTimeoutForMobile": 30,
            "enabled": true
        },
        "odds_boost_tab_branded": false,
        "optimizely:key": "LjyLvk2Y9sTHwfxHcsdQe",
        "optimizely:active": false,
        "pokerblast": {
            "disabled_on_ios": false,
            "disabled_on_android": false,
            "brands_blacklist": [
                0,
                1,
                58,
                81,
                84
            ]
        }
    },
    "user": {
        "regulationTypeData": {
            "tax_on_returns": null,
            "betbuilder_region": "Sweden",
            "login_restricted": 0
        },
        "opsGroupId": 3,
        "isEachwayAllowed": true,
        "isGtpBetBuilderEnabled": true,
        "isBogRestricted": true,
        "oddsFormat": "DECIMAL",
        "instructionCards": [],
        "favouriteSports": [],
        "themeMode": "2",
        "spectateSessionId": "f5f0d309-1cf3-49f8-93f2-4ae38117496f:anon"
    }
}
]

events:curl 'https://spectate-web.mrgreen.se/spectate/more_games/fetchEvents' \
  -H 'accept: */*' \
  -H 'accept-language: en-GB,en;q=0.9,sv;q=0.8' \
  -H 'cache-control: no-cache' \
  -H 'content-type: multipart/form-data; boundary=----WebKitFormBoundaryg9BUAmo8EdAZKBwc' \
  -b 'anon_hash=c94e2283da416020931e139fb433c981; odds_format=DECIMAL; 888Cookie=isftd%3Dfalse%26isHybrid%3Dfalse%26isreal%3Dfalse%26lang%3Dsv%26queryCountry%3Dswe%26queryState%3Dab; spectate_client_ver=2.145; bbsess=rqzg-7YqPIi5Nzf1bARnQMGqxhk; lang=swe; spectate_session=f5f0d309-1cf3-49f8-93f2-4ae38117496f%3Aanon; 888TestData=%7B%22orig-lp%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2Fsport%2Ffotboll%2Fspanien%2Fspanish-la-liga-primera%2Flevante-vs-elche-e-6973626%2F%22%2C%22currentvisittype%22%3A%22Unknown%22%2C%22strategy%22%3A%22UnknownStrategy%22%2C%22strategysource%22%3A%22previousvisit%22%2C%22referrer%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2F%22%7D; 888TestDataLocal=%7B%22datecreated%22%3A%222026-01-21T10%3A43%3A38.551Z%22%2C%22expiredat%22%3A%22Wed%2C%2028%20Jan%202026%2010%3A43%3A00%20GMT%22%2C%22datemodified%22%3A%222026-01-21T14%3A49%3A27.016Z%22%2C%22modifiedcounter%22%3A%222%22%2C%22trackingId%22%3A%22nepeYvvUP29yguF9uQ7K7hgGNWLPWNGP655BQMlcqka5xfyZ4Vnkvg%3D%3D%22%7D; 888Attribution=1' \
  -H 'origin: https://www.mrgreen.se' \
  -H 'pragma: no-cache' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.mrgreen.se/' \
  -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36' \
  --data-raw $'------WebKitFormBoundaryg9BUAmo8EdAZKBwc\r\nContent-Disposition: form-data; name="tournamentSlug"\r\n\r\nboxing-bouts\r\n------WebKitFormBoundaryg9BUAmo8EdAZKBwc\r\nContent-Disposition: form-data; name="categorySlug"\r\n\r\nboxing\r\n------WebKitFormBoundaryg9BUAmo8EdAZKBwc\r\nContent-Disposition: form-data; name="sportSlug"\r\n\r\nboxing\r\n------WebKitFormBoundaryg9BUAmo8EdAZKBwc\r\nContent-Disposition: form-data; name="tournamentId"\r\n\r\n9539\r\n------WebKitFormBoundaryg9BUAmo8EdAZKBwc\r\nContent-Disposition: form-data; name="eventId"\r\n\r\n6990486\r\n------WebKitFormBoundaryg9BUAmo8EdAZKBwc\r\nContent-Disposition: form-data; name="categoryId"\r\n\r\n3057\r\n------WebKitFormBoundaryg9BUAmo8EdAZKBwc\r\nContent-Disposition: form-data; name="sportId"\r\n\r\n354\r\n------WebKitFormBoundaryg9BUAmo8EdAZKBwc--\r\n'

  response:{
    "translations": {
        "category": "boxing",
        "tournament ": "boxing-bouts",
        "sport": "boxning"
    },
    "events": {
        "6922496": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922496,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "bakhram-murtazaliev-v-josh-kelly",
            "category_name": "Boxing",
            "event_l10n_slug": "bakhram-murtazaliev-v-josh-kelly",
            "racing_name": null,
            "name": "Bakhram Murtazaliev v Josh Kelly",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "38138": {
                    "is_home_team": true,
                    "slug_l10n_slug": "bakhram-murtazaliev",
                    "order": 1,
                    "id": 38138,
                    "slug": "bakhram-murtazaliev",
                    "name": "Bakhram Murtazaliev"
                },
                "10054": {
                    "is_home_team": false,
                    "slug_l10n_slug": "josh-kelly",
                    "order": 2,
                    "id": 10054,
                    "slug": "josh-kelly",
                    "name": "Josh Kelly"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6922497": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922497,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T03:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "teofimo-lopez-v-shakur-stevenson",
            "category_name": "Boxing",
            "event_l10n_slug": "teofimo-lopez-v-shakur-stevenson",
            "racing_name": null,
            "name": "Teofimo Lopez v Shakur Stevenson",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "60001": {
                    "is_home_team": true,
                    "slug_l10n_slug": "teofimo-lopez",
                    "order": 1,
                    "id": 60001,
                    "slug": "teofimo-lopez",
                    "name": "Teofimo Lopez"
                },
                "10047": {
                    "is_home_team": false,
                    "slug_l10n_slug": "shakur-stevenson",
                    "order": 2,
                    "id": 10047,
                    "slug": "shakur-stevenson",
                    "name": "Shakur Stevenson"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6922498": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922498,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T04:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "xander-zayas-v-abass-baraou",
            "category_name": "Boxing",
            "event_l10n_slug": "xander-zayas-v-abass-baraou",
            "racing_name": null,
            "name": "Xander Zayas v Abass Baraou",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "286432": {
                    "is_home_team": false,
                    "slug_l10n_slug": "abass-baraou",
                    "order": 2,
                    "id": 286432,
                    "slug": "abass-baraou",
                    "name": "Abass Baraou"
                },
                "145322": {
                    "is_home_team": true,
                    "slug_l10n_slug": "xander-zayas",
                    "order": 1,
                    "id": 145322,
                    "slug": "xander-zayas",
                    "name": "Xander Zayas"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6990486": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6990486,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-24T05:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-24",
            "active": true,
            "sport_slug": "boxing",
            "slug": "callum-walsh-v-carlos-ocampo",
            "category_name": "Boxing",
            "event_l10n_slug": "callum-walsh-v-carlos-ocampo",
            "racing_name": null,
            "name": "Callum Walsh v Carlos Ocampo",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "453741": {
                    "is_home_team": true,
                    "slug_l10n_slug": "callum-walsh",
                    "order": 1,
                    "id": 453741,
                    "slug": "callum-walsh",
                    "name": "Callum Walsh"
                },
                "209718": {
                    "is_home_team": false,
                    "slug_l10n_slug": "carlos-ocampo",
                    "order": 2,
                    "id": 209718,
                    "slug": "carlos-ocampo",
                    "name": "Carlos Ocampo"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "7013878": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013878,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "josh-padley-v-jaouad-belmehdi",
            "category_name": "Boxing",
            "event_l10n_slug": "josh-padley-v-jaouad-belmehdi",
            "racing_name": null,
            "name": "Josh Padley v Jaouad Belmehdi",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "325384": {
                    "is_home_team": true,
                    "slug_l10n_slug": "josh-padley",
                    "order": 1,
                    "id": 325384,
                    "slug": "josh-padley",
                    "name": "Josh Padley"
                },
                "212994": {
                    "is_home_team": false,
                    "slug_l10n_slug": "jaouad-belmehdi",
                    "order": 2,
                    "id": 212994,
                    "slug": "jaouad-belmehdi",
                    "name": "Jaouad Belmehdi"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "7013879": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013879,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "francesca-hennessy-v-ellie-bouttell",
            "category_name": "Boxing",
            "event_l10n_slug": "francesca-hennessy-v-ellie-bouttell",
            "racing_name": null,
            "name": "Francesca Hennessy v Ellie Bouttell",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "479873": {
                    "is_home_team": false,
                    "slug_l10n_slug": "ellie-bouttell",
                    "order": 2,
                    "id": 479873,
                    "slug": "ellie-bouttell",
                    "name": "Ellie Bouttell"
                },
                "266994": {
                    "is_home_team": true,
                    "slug_l10n_slug": "francesca-hennessy",
                    "order": 1,
                    "id": 266994,
                    "slug": "francesca-hennessy",
                    "name": "Francesca Hennessy"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6956824": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956824,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T02:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "carlos-adames-v-austin-williams",
            "category_name": "Boxing",
            "event_l10n_slug": "carlos-adames-v-austin-williams",
            "racing_name": null,
            "name": "Carlos Adames v Austin Williams",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "154666": {
                    "is_home_team": true,
                    "slug_l10n_slug": "carlos-adames",
                    "order": 1,
                    "id": 154666,
                    "slug": "carlos-adames",
                    "name": "Carlos Adames"
                },
                "75916": {
                    "is_home_team": false,
                    "slug_l10n_slug": "austin-williams",
                    "order": 2,
                    "id": 75916,
                    "slug": "austin-williams",
                    "name": "Austin Williams"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6956823": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956823,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T02:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "keyshawn-davis-v-jamaine-ortiz",
            "category_name": "Boxing",
            "event_l10n_slug": "keyshawn-davis-v-jamaine-ortiz",
            "racing_name": null,
            "name": "Keyshawn Davis v Jamaine Ortiz",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "123229": {
                    "is_home_team": true,
                    "slug_l10n_slug": "keyshawn-davis",
                    "order": 1,
                    "id": 123229,
                    "slug": "keyshawn-davis",
                    "name": "Keyshawn Davis"
                },
                "102158": {
                    "is_home_team": false,
                    "slug_l10n_slug": "jamaine-ortiz",
                    "order": 2,
                    "id": 102158,
                    "slug": "jamaine-ortiz",
                    "name": "Jamaine Ortiz"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "7013880": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013880,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T23:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "jacob-bank-v-william-scull",
            "category_name": "Boxing",
            "event_l10n_slug": "jacob-bank-v-william-scull",
            "racing_name": null,
            "name": "Jacob Bank v William Scull",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "308857": {
                    "is_home_team": false,
                    "slug_l10n_slug": "william-scull",
                    "order": 2,
                    "id": 308857,
                    "slug": "william-scull",
                    "name": "William Scull"
                },
                "486990": {
                    "is_home_team": true,
                    "slug_l10n_slug": "jacob-bank",
                    "order": 1,
                    "id": 486990,
                    "slug": "jacob-bank",
                    "name": "Jacob Bank"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6956822": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6956822,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-02-01T01:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-02-01",
            "active": true,
            "sport_slug": "boxing",
            "slug": "bruce-carrington-v-carlos-castro",
            "category_name": "Boxing",
            "event_l10n_slug": "bruce-carrington-v-carlos-castro",
            "racing_name": null,
            "name": "Bruce Carrington v Carlos Castro",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "206608": {
                    "is_home_team": true,
                    "slug_l10n_slug": "bruce-carrington",
                    "order": 1,
                    "id": 206608,
                    "slug": "bruce-carrington",
                    "name": "Bruce Carrington"
                },
                "128437": {
                    "is_home_team": false,
                    "slug_l10n_slug": "carlos-castro",
                    "order": 2,
                    "id": 128437,
                    "slug": "carlos-castro",
                    "name": "Carlos Castro"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "7013876": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013876,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "elif-nur-turhan-v-taylah-gentzen",
            "category_name": "Boxing",
            "event_l10n_slug": "elif-nur-turhan-v-taylah-gentzen",
            "racing_name": null,
            "name": "Elif Nur Turhan v Taylah Gentzen",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "383817": {
                    "is_home_team": false,
                    "slug_l10n_slug": "taylah-gentzen",
                    "order": 2,
                    "id": 383817,
                    "slug": "taylah-gentzen",
                    "name": "Taylah Gentzen"
                },
                "469598": {
                    "is_home_team": true,
                    "slug_l10n_slug": "elif-nur-turhan",
                    "order": 1,
                    "id": 469598,
                    "slug": "elif-nur-turhan",
                    "name": "Elif Nur Turhan"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "7013877": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 7013877,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T21:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "gradus-kraus-v-boris-crighton",
            "category_name": "Boxing",
            "event_l10n_slug": "gradus-kraus-v-boris-crighton",
            "racing_name": null,
            "name": "Gradus Kraus v Boris Crighton",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "248059": {
                    "is_home_team": false,
                    "slug_l10n_slug": "boris-crighton",
                    "order": 2,
                    "id": 248059,
                    "slug": "boris-crighton",
                    "name": "Boris Crighton"
                },
                "486989": {
                    "is_home_team": true,
                    "slug_l10n_slug": "gradus-kraus",
                    "order": 1,
                    "id": 486989,
                    "slug": "gradus-kraus",
                    "name": "Gradus Kraus"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6926166": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6926166,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-25T03:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-25",
            "active": true,
            "sport_slug": "boxing",
            "slug": "raymond-muratalla-v-andy-cruz",
            "category_name": "Boxing",
            "event_l10n_slug": "raymond-muratalla-v-andy-cruz",
            "racing_name": null,
            "name": "Raymond Muratalla v Andy Cruz",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "152106": {
                    "is_home_team": true,
                    "slug_l10n_slug": "raymond-muratalla",
                    "order": 1,
                    "id": 152106,
                    "slug": "raymond-muratalla",
                    "name": "Raymond Muratalla"
                },
                "123238": {
                    "is_home_team": false,
                    "slug_l10n_slug": "andy-cruz",
                    "order": 2,
                    "id": 123238,
                    "slug": "andy-cruz",
                    "name": "Andy Cruz"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6990487": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6990487,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-25T05:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-25",
            "active": true,
            "sport_slug": "boxing",
            "slug": "israil-madrimov-v-luis-david-salazar",
            "category_name": "Boxing",
            "event_l10n_slug": "israil-madrimov-v-luis-david-salazar",
            "racing_name": null,
            "name": "Israil Madrimov v Luis David Salazar",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "486213": {
                    "is_home_team": false,
                    "slug_l10n_slug": "luis-david-salazar",
                    "order": 2,
                    "id": 486213,
                    "slug": "luis-david-salazar",
                    "name": "Luis David Salazar"
                },
                "29822": {
                    "is_home_team": true,
                    "slug_l10n_slug": "israil-madrimov",
                    "order": 1,
                    "id": 29822,
                    "slug": "israil-madrimov",
                    "name": "Israil Madrimov"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6990488": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6990488,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-25T05:30:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-25",
            "active": true,
            "sport_slug": "boxing",
            "slug": "khalil-coe-v-jesse-hart",
            "category_name": "Boxing",
            "event_l10n_slug": "khalil-coe-v-jesse-hart",
            "racing_name": null,
            "name": "Khalil Coe v Jesse Hart",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "216336": {
                    "is_home_team": true,
                    "slug_l10n_slug": "khalil-coe",
                    "order": 1,
                    "id": 216336,
                    "slug": "khalil-coe",
                    "name": "Khalil Coe"
                },
                "486214": {
                    "is_home_team": false,
                    "slug_l10n_slug": "jesse-hart",
                    "order": 2,
                    "id": 486214,
                    "slug": "jesse-hart",
                    "name": "Jesse Hart"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        },
        "6922495": {
            "event_type": "NORMAL",
            "betable": true,
            "odds_boost_count": 0,
            "event_status": "PENDING",
            "inplay": false,
            "is_odds_boost": false,
            "id": 6922495,
            "tournament_id": 9539,
            "extra_info": [],
            "match_status": "PENDING",
            "tournament_name": "Boxing Bouts",
            "category_l10n_slug": "boxing",
            "sport_name": "Boxning",
            "sport_id": 354,
            "type": "NORMAL",
            "metadata": {
                "impact_sub": null
            },
            "category_slug": "boxing",
            "start_time": "2026-01-31T22:00:00+00:00",
            "tournament_slug": "boxing-bouts",
            "tournament_display_name": "Boxing Bouts",
            "scheduled_date": "2026-01-31",
            "active": true,
            "sport_slug": "boxing",
            "slug": "adam-azim-v-gustavo-daniel-lemos",
            "category_name": "Boxing",
            "event_l10n_slug": "adam-azim-v-gustavo-daniel-lemos",
            "racing_name": null,
            "name": "Adam Azim v Gustavo Daniel Lemos",
            "sport_l10n_slug": "boxning",
            "markets": {
                "8207": {
                    "name": "Fight Winner",
                    "betable": true,
                    "is_extra_time": false,
                    "tradable": true,
                    "boosted_odds": false,
                    "active": true,
                    "selection_schema": 1,
                    "id": 8207
                }
            },
            "tradable": false,
            "competitors": {
                "302324": {
                    "is_home_team": false,
                    "slug_l10n_slug": "gustavo-daniel-lemos",
                    "order": 2,
                    "id": 302324,
                    "slug": "gustavo-daniel-lemos",
                    "name": "Gustavo Daniel Lemos"
                },
                "152172": {
                    "is_home_team": true,
                    "slug_l10n_slug": "adam-azim",
                    "order": 1,
                    "id": 152172,
                    "slug": "adam-azim",
                    "name": "Adam Azim"
                }
            },
            "tournament_l10n_slug": "boxing-bouts",
            "category_id": 3057,
            "isUsFormat": false,
            "tournament_logos": null,
            "is_streaming_available": false
        }
    },
    "event_order": [
        {
            "event_id": 6990486
        },
        {
            "event_id": 6926166
        },
        {
            "event_id": 6990487
        },
        {
            "event_id": 6990488
        },
        {
            "event_id": 7013876
        },
        {
            "event_id": 7013877
        },
        {
            "event_id": 7013878
        },
        {
            "event_id": 7013879
        },
        {
            "event_id": 6922495
        },
        {
            "event_id": 6922496
        },
        {
            "event_id": 7013880
        },
        {
            "event_id": 6956822
        },
        {
            "event_id": 6956823
        },
        {
            "event_id": 6956824
        },
        {
            "event_id": 6922497
        },
        {
            "event_id": 6922498
        }
    ]
}
more events in relevant url:curl 'https://spectate-web.mrgreen.se/spectate/sportsbook/getEventData/boxing/boxing/boxing-bouts/callum-walsh-v-carlos-ocampo/6990486' \
  -H 'accept: */*' \
  -H 'accept-language: en-GB,en;q=0.9,sv;q=0.8' \
  -H 'cache-control: no-cache' \
  -b 'anon_hash=c94e2283da416020931e139fb433c981; odds_format=DECIMAL; 888Cookie=isftd%3Dfalse%26isHybrid%3Dfalse%26isreal%3Dfalse%26lang%3Dsv%26queryCountry%3Dswe%26queryState%3Dab; spectate_client_ver=2.145; bbsess=rqzg-7YqPIi5Nzf1bARnQMGqxhk; lang=swe; spectate_session=f5f0d309-1cf3-49f8-93f2-4ae38117496f%3Aanon; 888TestData=%7B%22orig-lp%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2Fsport%2Ffotboll%2Fspanien%2Fspanish-la-liga-primera%2Flevante-vs-elche-e-6973626%2F%22%2C%22currentvisittype%22%3A%22Unknown%22%2C%22strategy%22%3A%22UnknownStrategy%22%2C%22strategysource%22%3A%22previousvisit%22%2C%22referrer%22%3A%22https%3A%2F%2Fwww.mrgreen.se%2F%22%7D; 888TestDataLocal=%7B%22datecreated%22%3A%222026-01-21T10%3A43%3A38.551Z%22%2C%22expiredat%22%3A%22Wed%2C%2028%20Jan%202026%2010%3A43%3A00%20GMT%22%2C%22datemodified%22%3A%222026-01-21T14%3A49%3A27.016Z%22%2C%22modifiedcounter%22%3A%222%22%2C%22trackingId%22%3A%22nepeYvvUP29yguF9uQ7K7hgGNWLPWNGP655BQMlcqka5xfyZ4Vnkvg%3D%3D%22%7D; 888Attribution=1' \
  -H 'origin: https://www.mrgreen.se' \
  -H 'pragma: no-cache' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.mrgreen.se/' \
  -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'


json:
{
    "event": {
        "details": {
            "event": {
                "id": "6990486",
                "event_type_id": "1",
                "name": "Callum Walsh v Carlos Ocampo",
                "sport_id": "354",
                "tournament_id": "9539",
                "category_id": "3057",
                "home_team_id": "453741",
                "away_team_id": "209718",
                "status_id": "2",
                "scheduled_date": "2026-01-24",
                "scheduled_start": "2026-01-24T05:30:00+00:00",
                "actual_start": null,
                "slug": "callum-walsh-v-carlos-ocampo",
                "active": "1",
                "tradable": "0",
                "visible": "1",
                "is_bet_builder_eligible": false,
                "order": "1000",
                "trading_category": null,
                "has_results": "0",
                "restrict_cashout": "0",
                "last_modified": "2026-01-19 13:10:36",
                "created": "2026-01-14 20:53:16",
                "first_active_selection_at": "2026-01-14 20:53:17",
                "home_team_name": "Callum Walsh",
                "home_team_slug": "callum-walsh",
                "away_team_name": "Carlos Ocampo",
                "away_team_slug": "carlos-ocampo",
                "feed_event_id": "IHHOOr0KQxHP8aPO9nLswd4fiKc",
                "metadata": [],
                "isUsFormat": false,
                "is_streaming_available": false
            },
            "sport": {
                "id": "354",
                "name": "Boxning",
                "slug": "boxing",
                "display_name": "Boxning",
                "bet_accept_delay": "5",
                "exposure_limit": "100000.00",
                "night_exposure_limit_percentage": "8",
                "stake_factor": "1.00",
                "multi_stake_factor": "1.00",
                "order": "9",
                "inplay_order": "10",
                "default_market_id": "8207",
                "alter_default_market_id": null,
                "extra_default_market_id_1": null,
                "extra_default_market_id_2": null,
                "extra_default_market_id_3": null,
                "widget_default_market_id": null,
                "widget_alter_default_market_id": null,
                "active": "1",
                "is_virtual": "0",
                "ribbon": null,
                "restrict_cashout": "0",
                "created_at": "2020-02-18 10:01:27",
                "last_modified": "2025-03-13 12:26:51",
                "is_searchable": "1",
                "search_order": "21",
                "upa_enabled": "1"
            },
            "category": {
                "id": "3057",
                "sport_id": "354",
                "name": "Boxing",
                "display_name": "Boxing",
                "slug": "boxing",
                "order": "1000",
                "active": "1",
                "night_exposure_limit_percentage": null,
                "created_at": "2020-02-18 10:01:27",
                "last_modified": "2022-09-26 05:21:48",
                "visualization_provider": "BG",
                "identifier": "6c60af82d6f7ffa08d4b50f0af3c7f79"
            },
            "tournament": {
                "id": "9539",
                "category_id": "3057",
                "supertournament_id": null,
                "name": "Boxing Bouts",
                "display_name": "Boxing Bouts",
                "slug": "boxing-bouts",
                "order": "1000",
                "trading_category": "2",
                "active": "1",
                "country_code": null,
                "restrict_cashout": "0",
                "created_at": "2020-02-18 10:01:27",
                "last_modified": "2024-04-19 10:44:51",
                "identifier": "a0a3127e6ac2a6d1f6cb0e1bc98a560d",
                "tournament_logos": null
            }
        },
        "markets": {
            "markets_selections": {
                "8207": [
                    {
                        "market_id": "8207",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "1.133",
                        "id": "16613924115",
                        "name": "Callum Walsh",
                        "selection_db_name": "Callum Walsh",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": "1",
                        "translate": "0",
                        "market_name": "Fight Winner",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": "1",
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "-752",
                        "fraction_price": "2/15",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh"
                    },
                    {
                        "market_id": "8207",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "15.000",
                        "id": "16613924117",
                        "name": "Draw",
                        "selection_db_name": "Draw",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": "X",
                        "translate": "0",
                        "market_name": "Fight Winner",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": "2",
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1400",
                        "fraction_price": "14/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Draw"
                    },
                    {
                        "market_id": "8207",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "5.400",
                        "id": "16613924116",
                        "name": "Carlos Ocampo",
                        "selection_db_name": "Carlos Ocampo",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": "2",
                        "translate": "0",
                        "market_name": "Fight Winner",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": "3",
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+440",
                        "fraction_price": "22/5",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo"
                    }
                ],
                "8208": [
                    {
                        "market_id": "8208",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "2.050",
                        "id": "16620465288",
                        "name": "Callum Walsh  to win by KO/TKO/DQ/Technical Decision",
                        "selection_db_name": "1  to win by KO/TKO/DQ/Technical Decision",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Method Of Victory",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+105",
                        "fraction_price": "21/20",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh  to win by KO/TKO/DQ/Technical Decision"
                    },
                    {
                        "market_id": "8208",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "2.300",
                        "id": "16620465289",
                        "name": "Callum Walsh  to win on Points",
                        "selection_db_name": "1  to win on Points",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Method Of Victory",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+130",
                        "fraction_price": "13/10",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh  to win on Points"
                    },
                    {
                        "market_id": "8208",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "8.000",
                        "id": "16620465290",
                        "name": "Carlos Ocampo  to win by KO/TKO/DQ/Technical Decision",
                        "selection_db_name": "2  to win by KO/TKO/DQ/Technical Decision",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Method Of Victory",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+700",
                        "fraction_price": "7/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo  to win by KO/TKO/DQ/Technical Decision"
                    },
                    {
                        "market_id": "8208",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "12.000",
                        "id": "16620465287",
                        "name": "Carlos Ocampo  to win on Points",
                        "selection_db_name": "2  to win on Points",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Method Of Victory",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1100",
                        "fraction_price": "11/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo  to win on Points"
                    },
                    {
                        "market_id": "8208",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "15.000",
                        "id": "16620465286",
                        "name": "Draw",
                        "selection_db_name": "X",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Method Of Victory",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1400",
                        "fraction_price": "14/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Draw"
                    }
                ],
                "8205": [
                    {
                        "market_id": "8205",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "2.000",
                        "id": "16620465284",
                        "name": "Yes",
                        "selection_db_name": "Yes",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": "Yes",
                        "translate": "0",
                        "market_name": "Fight Goes Distance",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": "4",
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+100",
                        "fraction_price": "1/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Yes"
                    },
                    {
                        "market_id": "8205",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "1.727",
                        "id": "16620465285",
                        "name": "No",
                        "selection_db_name": "No",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": "No",
                        "translate": "0",
                        "market_name": "Fight Goes Distance",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": "5",
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "-138",
                        "fraction_price": "8/11",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "No"
                    }
                ],
                "8201": [
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "2.300",
                        "id": "16620465277",
                        "name": "Callum Walsh Points",
                        "selection_db_name": "1 Points",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+130",
                        "fraction_price": "13/10",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Points"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "4.600",
                        "id": "16620465278",
                        "name": "Callum Walsh Rounds 4-6",
                        "selection_db_name": "1 Rounds 4-6",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+360",
                        "fraction_price": "18/5",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Rounds 4-6"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "6.000",
                        "id": "16620465276",
                        "name": "Callum Walsh Rounds 7-8",
                        "selection_db_name": "1 Rounds 7-8",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+500",
                        "fraction_price": "5/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Rounds 7-8"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "6.400",
                        "id": "16620465281",
                        "name": "Callum Walsh Rounds 1-3",
                        "selection_db_name": "1 Rounds 1-3",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+540",
                        "fraction_price": "27/5",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Rounds 1-3"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "8.000",
                        "id": "16620465282",
                        "name": "Callum Walsh Rounds 9-10",
                        "selection_db_name": "1 Rounds 9-10",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+700",
                        "fraction_price": "7/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Rounds 9-10"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "12.000",
                        "id": "16620465280",
                        "name": "Carlos Ocampo Points",
                        "selection_db_name": "2 Points",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1100",
                        "fraction_price": "11/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Points"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "15.000",
                        "id": "16620465279",
                        "name": "Draw",
                        "selection_db_name": "X",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1400",
                        "fraction_price": "14/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Draw"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "17.000",
                        "id": "16620465274",
                        "name": "Carlos Ocampo Rounds 4-6",
                        "selection_db_name": "2 Rounds 4-6",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1600",
                        "fraction_price": "16/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Rounds 4-6"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "23.000",
                        "id": "16620465275",
                        "name": "Carlos Ocampo Rounds 9-10",
                        "selection_db_name": "2 Rounds 9-10",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+2200",
                        "fraction_price": "22/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Rounds 9-10"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "23.000",
                        "id": "16620465283",
                        "name": "Carlos Ocampo Rounds 1-3",
                        "selection_db_name": "2 Rounds 1-3",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+2200",
                        "fraction_price": "22/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Rounds 1-3"
                    },
                    {
                        "market_id": "8201",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "23.000",
                        "id": "16620465273",
                        "name": "Carlos Ocampo Rounds 7-8",
                        "selection_db_name": "2 Rounds 7-8",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Grouped Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+2200",
                        "fraction_price": "22/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Rounds 7-8"
                    }
                ],
                "8203": [
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "2.300",
                        "id": "16620465254",
                        "name": "Callum Walsh Wins On Points",
                        "selection_db_name": "1 Wins On Points",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+130",
                        "fraction_price": "13/10",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins On Points"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "11.000",
                        "id": "16620465264",
                        "name": "Callum Walsh Wins In Round 5",
                        "selection_db_name": "1 Wins In Round 5",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1000",
                        "fraction_price": "10/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 5"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "11.000",
                        "id": "16620465260",
                        "name": "Callum Walsh Wins In Round 7",
                        "selection_db_name": "1 Wins In Round 7",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1000",
                        "fraction_price": "10/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 7"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "11.000",
                        "id": "16620465270",
                        "name": "Callum Walsh Wins In Round 6",
                        "selection_db_name": "1 Wins In Round 6",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1000",
                        "fraction_price": "10/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 6"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "11.000",
                        "id": "16620465252",
                        "name": "Callum Walsh Wins In Round 8",
                        "selection_db_name": "1 Wins In Round 8",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1000",
                        "fraction_price": "10/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 8"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "12.000",
                        "id": "16620465265",
                        "name": "Callum Walsh Wins In Round 4",
                        "selection_db_name": "1 Wins In Round 4",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1100",
                        "fraction_price": "11/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 4"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "12.000",
                        "id": "16620465262",
                        "name": "Carlos Ocampo Wins On Points",
                        "selection_db_name": "2 Wins On Points",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1100",
                        "fraction_price": "11/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins On Points"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "13.000",
                        "id": "16620465251",
                        "name": "Callum Walsh Wins In Round 9",
                        "selection_db_name": "1 Wins In Round 9",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1200",
                        "fraction_price": "12/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 9"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "13.000",
                        "id": "16620465272",
                        "name": "Callum Walsh Wins In Round 3",
                        "selection_db_name": "1 Wins In Round 3",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1200",
                        "fraction_price": "12/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 3"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "15.000",
                        "id": "16620465256",
                        "name": "Draw",
                        "selection_db_name": "X",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1400",
                        "fraction_price": "14/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Draw"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "15.000",
                        "id": "16620465263",
                        "name": "Callum Walsh Wins In Round 10",
                        "selection_db_name": "1 Wins In Round 10",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1400",
                        "fraction_price": "14/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 10"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "17.000",
                        "id": "16620465258",
                        "name": "Callum Walsh Wins In Round 2",
                        "selection_db_name": "1 Wins In Round 2",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1600",
                        "fraction_price": "16/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 2"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "19.000",
                        "id": "16620465271",
                        "name": "Callum Walsh Wins In Round 1",
                        "selection_db_name": "1 Wins In Round 1",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+1800",
                        "fraction_price": "18/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Callum Walsh Wins In Round 1"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "34.000",
                        "id": "16620465266",
                        "name": "Carlos Ocampo Wins In Round 6",
                        "selection_db_name": "2 Wins In Round 6",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+3300",
                        "fraction_price": "33/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 6"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "34.000",
                        "id": "16620465268",
                        "name": "Carlos Ocampo Wins In Round 7",
                        "selection_db_name": "2 Wins In Round 7",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+3300",
                        "fraction_price": "33/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 7"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "34.000",
                        "id": "16620465267",
                        "name": "Carlos Ocampo Wins In Round 5",
                        "selection_db_name": "2 Wins In Round 5",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+3300",
                        "fraction_price": "33/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 5"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "36.000",
                        "id": "16620465255",
                        "name": "Carlos Ocampo Wins In Round 4",
                        "selection_db_name": "2 Wins In Round 4",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+3500",
                        "fraction_price": "35/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 4"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "36.000",
                        "id": "16620465259",
                        "name": "Carlos Ocampo Wins In Round 8",
                        "selection_db_name": "2 Wins In Round 8",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+3500",
                        "fraction_price": "35/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 8"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "36.000",
                        "id": "16620465261",
                        "name": "Carlos Ocampo Wins In Round 9",
                        "selection_db_name": "2 Wins In Round 9",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+3500",
                        "fraction_price": "35/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 9"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "36.000",
                        "id": "16620465250",
                        "name": "Carlos Ocampo Wins In Round 3",
                        "selection_db_name": "2 Wins In Round 3",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+3500",
                        "fraction_price": "35/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 3"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "41.000",
                        "id": "16620465269",
                        "name": "Carlos Ocampo Wins In Round 10",
                        "selection_db_name": "2 Wins In Round 10",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+4000",
                        "fraction_price": "40/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 10"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "46.000",
                        "id": "16620465253",
                        "name": "Carlos Ocampo Wins In Round 2",
                        "selection_db_name": "2 Wins In Round 2",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+4500",
                        "fraction_price": "45/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 2"
                    },
                    {
                        "market_id": "8203",
                        "is_bet_builder_eligible": false,
                        "active": true,
                        "tradable": true,
                        "visible": "1",
                        "market_active": "1",
                        "decimal_price": "46.000",
                        "id": "16620465257",
                        "name": "Carlos Ocampo Wins In Round 1",
                        "selection_db_name": "2 Wins In Round 1",
                        "order": null,
                        "sp_only": "0",
                        "special_odds_value": "",
                        "type": null,
                        "translate": "0",
                        "market_name": "Round Betting",
                        "starting_price": null,
                        "inplay": false,
                        "home_team_name": "Callum Walsh",
                        "away_team_name": "Carlos Ocampo",
                        "supplier_id": "7",
                        "supplier_name": "SSOL",
                        "selection_type_id": null,
                        "sport_id": "354",
                        "is_boosted_odds": false,
                        "old_decimal_price": null,
                        "betable": true,
                        "suspended": false,
                        "american_price": "+4500",
                        "fraction_price": "45/1",
                        "old_american_price": null,
                        "old_fraction_price": null,
                        "selection_name": "Carlos Ocampo Wins In Round 1"
                    }
                ]
            },
            "markets_details": {
                "8201": {
                    "id": "8201",
                    "name": "Grouped Round Betting",
                    "translate": "0",
                    "columns": "3",
                    "selection_schema": "63",
                    "restrict_cashout": "1",
                    "default_line": null,
                    "is_bet_builder_eligible": false,
                    "is_odds_boost": false,
                    "is_impact_sub": false,
                    "isUsFormat": false
                },
                "8203": {
                    "id": "8203",
                    "name": "Vinnande rond",
                    "translate": "0",
                    "columns": "3",
                    "selection_schema": "60",
                    "restrict_cashout": "1",
                    "default_line": null,
                    "is_bet_builder_eligible": false,
                    "is_odds_boost": false,
                    "is_impact_sub": false,
                    "isUsFormat": false
                },
                "8205": {
                    "id": "8205",
                    "name": "Fight Goes Distance",
                    "translate": "0",
                    "columns": "2",
                    "selection_schema": "13",
                    "restrict_cashout": "0",
                    "default_line": null,
                    "is_bet_builder_eligible": false,
                    "is_odds_boost": false,
                    "is_impact_sub": false,
                    "isUsFormat": false
                },
                "8207": {
                    "id": "8207",
                    "name": "Fightodds",
                    "translate": "0",
                    "columns": "3",
                    "selection_schema": "1",
                    "restrict_cashout": "0",
                    "default_line": null,
                    "is_bet_builder_eligible": false,
                    "is_odds_boost": false,
                    "is_impact_sub": false,
                    "isUsFormat": false
                },
                "8208": {
                    "id": "8208",
                    "name": "Vinstmetod",
                    "translate": "0",
                    "columns": "3",
                    "selection_schema": "60",
                    "restrict_cashout": "1",
                    "default_line": null,
                    "is_bet_builder_eligible": false,
                    "is_odds_boost": false,
                    "is_impact_sub": false,
                    "isUsFormat": false
                }
            },
            "markets_selections_order": [
                8207,
                8208,
                8205,
                8201,
                8203
            ]
        },
        "articleData": {
            "spotlight": null,
            "articles": null
        },
        "filter_markets": false,
        "event_builder_extra_info": [],
        "scoreboard": {
            "daytime_start": null,
            "daytime_end": null
        },
        "statsCenterSupported": false
    },
    "cashout_enabled": false,
    "event_has_boosted_odds": false,
    "betbuilder_info": {
        "isBetbuilderEnabled": true,
        "betbuilderSource": "https://888sport-prod-gen2.sportcastlive.com?key=0a4bd4e3-4509-4de7-9b80-23018b69cfd6",
        "betbuilderIframApi": "https://cdn.betstream.betgenius.com/betstream-view/public/bg_api.js"
    }
}

