import berserk
import chess
import openai
import re
import time
import random
from threading import Thread
from dotenv import load_dotenv; load_dotenv()
import os


LICHESS_TOKEN = os.environ['LICHESS_TOKEN']
USERNAME = os.environ['LICHESS_USERNAME'] # could be obtained from token but i'm lazy
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
openai.api_key = OPENAI_API_KEY

PROMPT_HEADER = """[Event "Shamkir Chess"]
[White "Anand, Viswanathan"]
[Black "Topalov, Veselin"]
[Result "{result}"]
[WhiteElo "2779"]
[BlackElo "2740"]


"""

# dict of game_id: (thread, init_event)
RUNNING_GAMES = {}

def legal_move(board, move):
    try:
        board.push_san(move)
        board.pop()
        return True
    except ValueError:
        return False


def play_game(client, init_event):
    game = init_event['game']
    our_color = chess.WHITE if game['color'] == 'white' else chess.BLACK
    prompt_header = PROMPT_HEADER.format(result='1-0' if our_color==chess.WHITE else '0-1')

    for event in client.bots.stream_game_state(game['id']):
        if event['type'] == 'chatLine':
            print(event)
            continue
        elif event['type'] == 'opponentGone':
            print('not handling opponentGone')
            continue

        game_state = event['state'] if event['type'] == 'gameFull' else event

        print('game event', event)

        prompt = prompt_header

        # set board position and prompt
        board = chess.Board()
        print(board)
        moves = [x for x in game_state['moves'].split(' ') if x]
        for i, move in enumerate(moves):
            # convert move from uci to san
            move_san = board.san(chess.Move.from_uci(move))
            board.push_uci(move)
            prompt += f'{i//2+1}. {move_san}' if i % 2 == 0 else f' {move_san}\n'

        if board.is_game_over():
            print('game over, result:', board.result())
            break

        # move if our turn
        if board.turn == our_color:
            print('PROMPT:\n' + prompt)
            move = ""
            while not legal_move(board, move):
                completion = openai.Completion.create(
                    engine="gpt-3.5-turbo-instruct",
                    prompt=prompt,
                    temperature=0.5,
                    max_tokens=6,
                )
                text = completion['choices'][0]['text'].strip() # type:ignore
                print('text', text)

                # match uci move with regex
                san_regex = re.compile(r"([KQBNR]?[a-h]?[1-8]?x?[a-h][1-8](?:=[KQBNR])?|O-O(?:-O)?|[a-h]x[a-h])(\+{1,2}|#)?")
                match = san_regex.search(text)
                print('match', match)
                if match:
                    move = match.group(0)
                    print('move', move)

            board.push_san(move)
            # convert move to uci and send
            client.bots.make_move(game['id'], board.peek().uci())
        else:
            print("not our turn")

    print('Finished game', game['id'])
    del RUNNING_GAMES[game['id']]


def look_for_games(client: berserk.Client):
    while True:
        if len(RUNNING_GAMES) < 4:
            our_rating = client.users.get_public_data(USERNAME)['perfs']['bullet']['rating']
            print(f'Looking for a game around {our_rating}...')
            bots = list(client.bots.get_online_bots(limit=100))
            random.shuffle(bots)
            for bot in bots:
                their_rating = bot['perfs']['bullet']['rating']
                if abs(their_rating - our_rating) < 100:
                    print('Challenging bot', bot['username'], 'with rating', their_rating, '...')
                    client.challenges.create(
                        username=bot['username'],
                        rated=True,
                        clock_limit=60,
                        clock_increment=0,
                        color=random.choice(['white', 'black']),
                    )
                    time.sleep(3)
                    if len(RUNNING_GAMES) >= 4:
                        break
                    else:
                        print(f"Only {len(RUNNING_GAMES)} games started, continuing to look for more...")


        time.sleep(5)



def main():
    session = berserk.TokenSession(LICHESS_TOKEN)
    client = berserk.Client(session=session)

    # Start games if possible
    t = Thread(target=look_for_games, args=[client], daemon=True)
    t.start()

    # Stream ongoing games
    print("Started. Waiting for events...")
    for event in client.bots.stream_incoming_events():
        print("Event received:", event)

        if event['type'] == 'challenge':
            try:
                client.bots.accept_challenge(event['challenge']['id'])
            except berserk.exceptions.ResponseError:
                print(f'Challenge {event["challenge"]["id"]} already accepted')
        elif event['type'] == 'gameStart':
            t = Thread(target=play_game, args=[client, event], daemon=True)
            t.start()
            RUNNING_GAMES[event['game']['id']] = (t, event)


if __name__ == '__main__':
    main()

