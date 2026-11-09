import random as ran


class Player:
    def __init__(self, id, card_count):
        self.id = id
        self.card_count = card_count
        self.hand = []

    def Change_card_count(self, change):
        self.card_count += change

    def Draw(self, deck):
        for i in range(self.card_count):
            self.hand.append(deck.pop(0))

    def Legal(self, previous, current):
        #Returns True for legal move and False for illegal
        rank = {'lower': 0, 'middle': 1, 'face': 2, 'na': 3}
        if current[1] > previous[1]:
            return True
        elif current[1] == previous[1]:
            if rank[previous[0]] < rank[current[0]]:
                return True
        return False

    def Make_guess(self, previous):
        while True:
            card_type = input("Which card type (Lower, middle, Face): ").strip().lower()
            if card_type != 'lower' and card_type != 'middle' and card_type != 'face':
                print('Please type: lower, middle, or face')
                continue
            try:
                quantity = int(input('How many: '))
            except ValueError:
                print('Number please...')
                continue
            current = [card_type, quantity]
            if not self.Legal(previous, current):
                print("not a legal move (make sure guess is higher than previous player's guess)")
                continue
            return card_type, quantity
            
    def Turn(self, first_turn, guess):
        if first_turn:
            return self.Make_guess(guess)
        while True:
            choice = input(guess[0] + ' ' + str(guess[1]) + " Do you: Guess, Fiddle, or riddle?: ").strip().lower()
            if choice == 'fiddle': 
                print('fiddle')
                return 'fiddle'
            if choice == 'riddle':
                print('riddle')
                return 'riddle'
            if choice == 'guess':
                return self.Make_guess(guess)
            print('Please type: Guess, Fiddle, or riddle')
                
    def Counting_cards(self):
        lower = 0
        middle = 0
        face = 0
        for i in range(len(self.hand)):
            card = self.hand[i][1]
            if card == 1:
                lower += 1
                middle += 1
                face += 1
            if 1 < card < 7:
                 lower += 1
            if 6 < card < 11:
                middle += 1
            if 10 < card:
                face += 1
        return lower, middle, face


class Game:
    def __init__(self, Num_players):
        self.Num_players = Num_players
        self.player_list = self.Initial_players()

    def Shuffle_deck(self):
        #Creating deck
        deck = []
        for i in range(1, 5):
            for j in range(1, 14):
                deck.append([i, j])

        #Adding Jokers
        deck.append([0, -1])
        deck.append([0, 1])

        shuffled_deck = deck
        ran.shuffle(shuffled_deck)

        return shuffled_deck

    def Redeal(self):
        deck = self.Shuffle_deck()
        for player in self.player_list:
            player.hand = []
            player.Draw(deck)

    def Initial_players(self):
        #Function creates num_players number of players (in the Player class)
        player_list = []
        for i in range(1, self.Num_players + 1):
            player_list.append(Player(i, 6))
        return player_list

    def Counting_Total_Card(self, card_type_bet):
        count = 0
        if card_type_bet == 'lower':
            card_type_bet = 0
        elif card_type_bet == 'middle':
            card_type_bet = 1
        elif card_type_bet == 'face':
            card_type_bet = 2
        for i in range(len(self.player_list)):
            count += self.player_list[i].Counting_cards()[card_type_bet]
        return count

    def Deciding_winner_fiddle(self, count, guess, current_player):
        if guess <= count:
            self.player_list[current_player].Change_card_count(-1)
        else:
            self.player_list[current_player - 1].Change_card_count(-1)

    def Deciding_winner_riddle(self, count, guess, current_player):
        if guess == count:
            self.player_list[current_player].Change_card_count(1)
        else:
            self.player_list[current_player].Change_card_count(-2)

    def Round(self, starting_player):
        first_turn = True
        past_guess = ['na', 0]
        guess = past_guess
        current_player = starting_player
        while guess != 'fiddle' and guess != 'riddle':
            past_guess = guess
            guess = self.player_list[current_player].Turn(first_turn, guess)
            first_turn = False
            current_player += 1
            if current_player == len(self.player_list):
                current_player = 0
        current_player -= 1
        count = self.Counting_Total_Card(past_guess[0])
        if guess == 'fiddle':
            self.Deciding_winner_fiddle(count, past_guess[1], current_player)
        if guess == 'riddle':
            self.Deciding_winner_riddle(count, past_guess[1], current_player)
        for player in self.player_list:
            print(player.card_count)

    def Start_Game(self):
        while True:
            new_player_list = []
            for player in self.player_list:
                if player.card_count > 0:
                    new_player_list.append(player)
            self.player_list = new_player_list
            if len(self.player_list) < 2:
                break
            self.Redeal()
            self.Round(0)
            

Game(3).Start_Game()

